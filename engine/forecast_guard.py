import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from .data_feed import WeatherState
from .config import QuantConfig

logger = logging.getLogger("ForecastGuard")


class ForecastGuardManager:
    """Forecast Guard V2: periodic risk lock + unlock checks."""

    def __init__(self, config: QuantConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "WeatherBotForecastGuard/1.0 (dev@example.com)"}
        )
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._recording_files: Dict[str, str] = {}  # 缓存会话文件名，确保单次运行内固定写入同一文件

    def assess(self, preset_name: str, state: WeatherState, conf: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "enabled": bool(self.config.FORECAST_GUARD_ENABLED),
            "locked": False,
            "reason": "",
            "risk_count": 0,
            "available_sources": 0,
            "sources": {},
        }
        if not self.config.FORECAST_GUARD_ENABLED:
            return result

        now_utc = datetime.now(timezone.utc)
        entry = self._cache.setdefault(
            preset_name,
            {
                "last_refresh_utc": None,
                "report": None,
                "locked": False,
                "lock_reason": "",
                "lock_peak_utc": None,
                "noaa_anchor_miss_streak": 0,
            },
        )

        # NOAA anchor fail-safe with streak: do not lock on one-off network jitter.
        if state.noaa_curr is None:
            entry["noaa_anchor_miss_streak"] = int(entry.get("noaa_anchor_miss_streak", 0)) + 1
            lock_streak = max(1, int(getattr(self.config, "FORECAST_GUARD_NOAA_ANCHOR_LOCK_STREAK", 3)))
            should_lock = (
                bool(self.config.FORECAST_GUARD_FAIL_SAFE)
                and entry["noaa_anchor_miss_streak"] >= lock_streak
            )
            result["locked"] = should_lock
            result["reason"] = "No NOAA anchor"
            # Expose streak for debugging/recording.
            result["noaa_anchor_miss_streak"] = entry["noaa_anchor_miss_streak"]
            result["noaa_anchor_lock_streak"] = lock_streak
            return result
        else:
            entry["noaa_anchor_miss_streak"] = 0

        need_refresh = True
        if entry["last_refresh_utc"] is not None:
            elapsed = (now_utc - entry["last_refresh_utc"]).total_seconds()
            need_refresh = elapsed >= self.config.FORECAST_GUARD_RECALC_INTERVAL_SECONDS

        if need_refresh:
            try:
                report = self._compute_report(now_utc, state, conf, preset_name)
            except Exception as exc:
                logger.warning("Forecast Guard compute failed for %s: %s", preset_name, exc)
                report = {
                    "risk_count": 0,
                    "available_sources": 0,
                    "sources": {},
                    "summary_reason": f"Guard fetch failed: {exc}",
                    "latest_risky_peak_utc": None,
                    "future_2h_warming": {},
                    "avg_afternoon_peak": None,
                    "avg_night_peak": None,
                    "max_bias": 0.0,
                    "max_2h_warming": 0.0
                }
            entry["report"] = report
            entry["last_refresh_utc"] = now_utc

        report = entry.get("report") or {}
        result.update(
            {
                "risk_count": int(report.get("risk_count", 0)),
                "available_sources": int(report.get("available_sources", 0)),
                "sources": report.get("sources", {}),
                "avg_afternoon_peak": report.get("avg_afternoon_peak"),
                "avg_night_peak": report.get("avg_night_peak"),
                "max_bias": report.get("max_bias"),
                "max_2h_warming": report.get("max_2h_warming"),
                "latest_risky_peak_utc": report.get("latest_risky_peak_utc"),
            }
        )

        available = int(report.get("available_sources", 0))
        risk_count = int(report.get("risk_count", 0))
        # 多源投票保护：至少 2 个风险源才允许进入锁仓，避免单源误报直接锁死。
        threshold = max(2, int(self.config.FORECAST_GUARD_RISK_SOURCE_THRESHOLD))
        no_data_lock = available == 0 and bool(self.config.FORECAST_GUARD_FAIL_SAFE)

        if risk_count >= threshold or no_data_lock:
            entry["locked"] = True
            entry["lock_reason"] = report.get("summary_reason", "Forecast warming risk")
            risky_peak = report.get("latest_risky_peak_utc")
            if risky_peak is not None:
                prev = entry.get("lock_peak_utc")
                if prev is None or risky_peak > prev:
                    entry["lock_peak_utc"] = risky_peak

        if entry.get("locked"):
            if self._can_unlock(now_utc, entry, report, state):
                entry["locked"] = False
                entry["lock_reason"] = "Unlocked: post-peak cooling confirmed"
                entry["lock_peak_utc"] = None
            else:
                result["locked"] = True
                result["reason"] = entry.get("lock_reason") or "ForecastGuard locked"
                return result

        result["locked"] = False
        result["reason"] = report.get("summary_reason", "Guard pass")
        return result

    def _can_unlock(
        self,
        now_utc: datetime,
        entry: Dict[str, Any],
        report: Dict[str, Any],
        state: WeatherState,
    ) -> bool:
        peak_utc = entry.get("lock_peak_utc")
        if peak_utc is not None:
            if now_utc < peak_utc + timedelta(minutes=self.config.FORECAST_GUARD_PEAK_PASSED_MINUTES):
                return False

        if not self._measurements_cooling(state):
            return False

        if not self._forecast_cooling(report):
            return False

        return True

    def _measurements_cooling(self, state: WeatherState) -> bool:
        def drop_ok(vals: List[float], min_drop: float) -> bool:
            if len(vals) < 3:
                return False
            a, b, c = vals[-3], vals[-2], vals[-1]
            return (a > b > c) and ((a - c) >= min_drop - 1e-9)

        noaa_ok = drop_ok(state.noaa_history, self.config.FORECAST_GUARD_UNLOCK_NOAA_DROP_C)
        aux_ok = drop_ok(state.om_history, self.config.FORECAST_GUARD_UNLOCK_AUX_DROP_C) or drop_ok(
            state.mn_history, self.config.FORECAST_GUARD_UNLOCK_AUX_DROP_C
        )
        return noaa_ok and aux_ok

    def _forecast_cooling(self, report: Dict[str, Any]) -> bool:
        warming = report.get("future_2h_warming", {})
        cool_count = 0
        for val in warming.values():
            if val is None:
                continue
            if val <= self.config.FORECAST_GUARD_UNLOCK_FUTURE_WARMING_C:
                cool_count += 1
        return cool_count >= 2

    def _compute_report(self, now_utc: datetime, state: WeatherState, conf: Dict[str, Any], preset_name: str) -> Dict[str, Any]:
        lat = float(conf["lat"])
        lon = float(conf["lon"])
        tz_offset = float(conf.get("tz_offset", 0))

        source_series = self._fetch_forecast_sources(lat, lon)
        if not source_series:
            return {
                "risk_count": 0,
                "available_sources": 0,
                "sources": {},
                "summary_reason": "No forecast sources available",
                "latest_risky_peak_utc": None,
                "future_2h_warming": {},
            }

        source_reports: Dict[str, Any] = {}
        risk_count = 0
        latest_risky_peak: Optional[datetime] = None
        future_2h_warming: Dict[str, Optional[float]] = {}

        day_max_so_far = state.max_temp_overall if state.max_temp_overall > -900 else state.noaa_curr
        local_date = (now_utc + timedelta(hours=tz_offset)).date()

        for src_name, pts in source_series.items():
            if not pts:
                continue
            # Filter to local day only.
            day_pts = []
            for dt_utc, temp in pts:
                if (dt_utc + timedelta(hours=tz_offset)).date() == local_date:
                    day_pts.append((dt_utc, temp))
            if not day_pts:
                continue

            now_temp_raw = self._nearest_temp(day_pts, now_utc)
            if now_temp_raw is None:
                continue

            bias = state.noaa_curr - now_temp_raw
            corrected = [(dt, temp + bias) for dt, temp in day_pts]

            # 1. 提取波峰并确定日间基准 (Day Reference Max)
            peaks = self._extract_peaks(corrected, tz_offset)
            
            # 日间基准定义：12:00-17:00 之间的预报最高点，对比实测已发生的最高温
            afternoon_forecast_pts = [t for dt, t in corrected if 12 <= self._local_hour(dt, tz_offset) < 17]
            afternoon_forecast_max = max(afternoon_forecast_pts) if afternoon_forecast_pts else -999.0
            day_ref_max = max(afternoon_forecast_max, day_max_so_far)

            # 2. 识别夜间局部峰值 (用于观测/兼容字段)
            night_peaks = [p for p in peaks if p["local_hour"] >= 17]

            # 3. 核心判定：夜间风险区间窗口必须同时满足
            # - 17:00 后 T >= (day_ref_max - threshold) 的连续区间
            # - 持续点数 >= min_points
            # - 峰值显著性 >= prominence (采用区间内峰值/区间边界 proxy 的最大值)
            risk_window = self._find_valid_night_risk_window(corrected, tz_offset, day_ref_max)

            c_night_peak = risk_window is not None
            risk_desc = "OK"
            night_peak_dt = None  # 用于展示 (max@time)
            night_peak_anchor_dt = None  # 用于锁仓时间锚点 (window end)
            night_peak_temp = None
            if risk_window is not None:
                night_peak_dt = risk_window["max_time"]
                night_peak_anchor_dt = risk_window["end_time"]
                night_peak_temp = risk_window["max_temp"]
                risk_desc = (
                    "夜间风险区间"
                    f"[{risk_window['start_local_hour']:.1f}-{risk_window['end_local_hour']:.1f}h,"
                    f"max={risk_window['max_temp']:.1f}C@{risk_window['max_local_hour']:.1f}h,"
                    f"dur={risk_window['duration_points']}pts,"
                    f"prom_max={risk_window['prominence_max_c']:.2f}C]"
                )

            risky = c_night_peak
            if risky:
                risk_count += 1
                # 解锁锚点使用“风险窗口结束时间”，避免 plateau/多峰时用到过早的峰值导致提前解锁。
                anchor = night_peak_anchor_dt or night_peak_dt
                if anchor is not None:
                    if latest_risky_peak is None or anchor > latest_risky_peak:
                        latest_risky_peak = anchor

            # 4. 提取汇总指标用于返回
            future = [(dt, t) for dt, t in corrected if dt >= now_utc and self._local_hour(dt, tz_offset) < 24]
            future_max = max([t for _, t in future]) if future else state.noaa_curr
            
            future_2h = [(dt, t) for dt, t in corrected if now_utc <= dt <= now_utc + timedelta(hours=2)]
            future_2h_max = max([t for _, t in future_2h]) if future_2h else None
            future_2h_warming[src_name] = (
                (future_2h_max - state.noaa_curr) if future_2h_max is not None else None
            )

            # 保持接口兼容性，填充 source_reports
            source_reports[src_name] = {
                "bias_c": round(bias, 3),
                "future_max_c": round(future_max, 3),
                "afternoon_peak_c": round(day_ref_max, 3) if day_ref_max > -900 else None,
                "night_peak_c": (
                    round(night_peak_temp, 3)
                    if night_peak_temp is not None
                    else (round(night_peaks[0]["temp"], 3) if night_peaks else None)
                ),
                "night_peak_time_utc": night_peak_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if night_peak_dt else None,
                "night_peak_anchor_time_utc": (
                    night_peak_anchor_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if night_peak_anchor_dt else None
                ),
                "night_peak_duration_pts": int(risk_window["duration_points"]) if risk_window is not None else 0,
                "night_peak_prominence_c": (
                    round(risk_window["prominence_max_c"], 3) if risk_window is not None else None
                ),
                "night_risk_window_start_utc": (
                    risk_window["start_time"].strftime("%Y-%m-%dT%H:%M:%SZ") if risk_window is not None else None
                ),
                "night_risk_window_end_utc": (
                    risk_window["end_time"].strftime("%Y-%m-%dT%H:%M:%SZ") if risk_window is not None else None
                ),
                "c_night_peak": c_night_peak,
                "risky": risky,
                "risk_desc": risk_desc
            }

        # 汇总所有风险源的具体原因
        all_reasons = set()
        for r in source_reports.values():
            if r["risky"]:
                all_reasons.add(r["risk_desc"])

        if risk_count > 0:
            summary = f"Risky forecast sources {risk_count} [{' '.join(sorted(all_reasons))}]"
        else:
            summary = "No warming risk detected"

        # 提取汇总指标用于主日志
        afternoon_peaks = [r["afternoon_peak_c"] for r in source_reports.values() if r["afternoon_peak_c"] is not None]
        night_peaks = [r["night_peak_c"] for r in source_reports.values() if r["night_peak_c"] is not None]
        biases = [r["bias_c"] for r in source_reports.values()]
        warmings = [w for w in future_2h_warming.values() if w is not None]

        report_summary = {
            "risk_count": risk_count,
            "available_sources": len(source_reports),
            "sources": source_reports,
            "summary_reason": summary,
            "latest_risky_peak_utc": latest_risky_peak,
            "future_2h_warming": future_2h_warming,
            # 汇总指标
            "avg_afternoon_peak": sum(afternoon_peaks)/len(afternoon_peaks) if afternoon_peaks else None,
            "avg_night_peak": sum(night_peaks)/len(night_peaks) if night_peaks else None,
            "max_bias": max(biases) if biases else 0.0,
            "max_2h_warming": max(warmings) if warmings else 0.0
        }

        # [NEW] 记录全天原始预报数据以便回测
        self._record_raw_forecasts(preset_name, tz_offset, now_utc, source_series, source_reports)

        return report_summary

    def _record_raw_forecasts(
        self, 
        preset_name: str, 
        tz_offset: float, 
        now_utc: datetime, 
        source_series: Dict[str, List[Tuple[datetime, float]]],
        source_reports: Dict[str, Any]
    ):
        """记录每 30 分钟拉取的原始预报曲线 (优化后的水平格式)"""
        import os
        import csv
        
        data_dir = "data/recordings"
        os.makedirs(data_dir, exist_ok=True)
        
        # 按照系统本地运行时间命名文件，确保每次运行内固定写入同一文件 (Session Level)
        if preset_name not in self._recording_files:
            now_system = datetime.now()
            local_date_str = now_system.strftime("%Y%m%d")
            local_time_str = now_system.strftime("%H%M")
            self._recording_files[preset_name] = f"{data_dir}/forecast_raw_{preset_name}_{local_date_str}_{local_time_str}.csv"
        
        filename = self._recording_files[preset_name]
        
        # 定义当地时间用于数据小时对齐
        local_now = now_utc + timedelta(hours=tz_offset)
        
        file_exists = os.path.isfile(filename)
        timestamp_system = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timestamp_utc = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        timestamp_local = local_now.strftime("%Y-%m-%d %H:%M:%S")
        
        # 定义列：12点到24点 (增加明确的本地小时说明)
        hour_cols = [str(h) for h in range(12, 25)]
        header_map = {str(h): f"Local_{h}h" for h in range(12, 25)}
        fieldnames = ['timestamp_system', 'timestamp_utc', 'timestamp_local', 'source'] + [header_map[h] for h in hour_cols]
        
        try:
            with open(filename, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                
                for src_name, pts in source_series.items():
                    report = source_reports.get(src_name, {})
                    bias = report.get("bias_c", 0.0)
                    
                    row_data = {
                        'timestamp_system': timestamp_system,
                        'timestamp_utc': timestamp_utc,
                        'timestamp_local': timestamp_local,
                        'source': src_name
                    }
                    for dt_utc, temp in pts:
                        loc_dt = dt_utc + timedelta(hours=tz_offset)
                        if loc_dt.date() != local_now.date():
                            if loc_dt.date() == (local_now + timedelta(days=1)).date() and loc_dt.hour == 0:
                                h_key = "24"
                            else:
                                continue
                        else:
                            h_key = str(loc_dt.hour)
                        
                        if h_key in hour_cols:
                            row_data[header_map[h_key]] = round(temp + bias, 2)
                    
                    writer.writerow(row_data)
        except Exception as e:
            logger.error(f"[{preset_name}] Failed to record raw forecasts: {e}")

    @staticmethod
    def _nearest_temp(points: List[Tuple[datetime, float]], ref_dt: datetime) -> Optional[float]:
        if not points:
            return None
        best = min(points, key=lambda x: abs((x[0] - ref_dt).total_seconds()))
        return best[1]

    def _find_valid_night_risk_window(
        self,
        series: List[Tuple[datetime, float]],
        tz_offset: float,
        day_ref_max: float,
    ) -> Optional[Dict[str, Any]]:
        """Find a night risk *window* with persistence + prominence filters.

        This replaces the old "first local peak" rule to avoid plateau/multi-peak cases
        anchoring too early (e.g. reporting 17:00 while the max persists to 22:00).
        """
        rebound_threshold = float(self.config.FORECAST_GUARD_PEAK_THRESHOLD_C)
        min_points = max(1, int(self.config.FORECAST_GUARD_PEAK_MIN_POINTS))
        min_prominence = max(0.0, float(self.config.FORECAST_GUARD_PEAK_PROMINENCE_C))
        risk_line = day_ref_max - rebound_threshold

        # Keep only 12:00-24:00 local points for the local day (caller already filtered date).
        day_points: List[Tuple[datetime, float, float]] = []
        for dt_utc, temp in series:
            local_hour = self._local_hour(dt_utc, tz_offset)
            if 12 <= local_hour < 24:
                day_points.append((dt_utc, temp, local_hour))

        if len(day_points) < 3:
            return None

        # Build contiguous night segments where temp >= risk_line.
        segments: List[Tuple[int, int]] = []
        start = None
        for i, (_, temp, hour) in enumerate(day_points):
            in_night = hour >= 17
            ok = in_night and (temp >= risk_line)
            if ok and start is None:
                start = i
            if (not ok) and start is not None:
                segments.append((start, i - 1))
                start = None
        if start is not None:
            segments.append((start, len(day_points) - 1))

        def local_peak_prom(i: int) -> Optional[float]:
            if i <= 0 or i >= len(day_points) - 1:
                return None
            curr = day_points[i][1]
            prev = day_points[i - 1][1]
            nxt = day_points[i + 1][1]
            if not (curr > prev and curr >= nxt):
                return None
            return curr - max(prev, nxt)

        best: Optional[Dict[str, Any]] = None
        eps = 1e-9
        for left, right in segments:
            duration = right - left + 1
            if duration < min_points:
                continue

            temps = [day_points[j][1] for j in range(left, right + 1)]
            max_temp = max(temps)
            max_indices = [j for j in range(left, right + 1) if abs(day_points[j][1] - max_temp) <= 1e-6]
            max_idx = max(max_indices)  # latest max -> better representative time

            # Prominence within window: require at least one local peak inside the window.
            # This preserves the legacy intent: avoid flagging a purely monotonic cooling tail
            # after 17:00 as "risk" just because it is still above the risk line.
            prom_peaks = []
            for j in range(left, right + 1):
                p = local_peak_prom(j)
                if p is not None:
                    prom_peaks.append(p)
            if not prom_peaks:
                continue
            prom_max = max(prom_peaks)

            if prom_max + eps < min_prominence:
                continue

            start_dt, _, start_hour = day_points[left]
            end_dt, _, end_hour = day_points[right]
            max_dt, _, max_hour = day_points[max_idx]

            candidate = {
                "start_time": start_dt,
                "end_time": end_dt,
                "start_local_hour": start_hour,
                "end_local_hour": end_hour,
                "max_time": max_dt,
                "max_local_hour": max_hour,
                "max_temp": max_temp,
                "duration_points": duration,
                "prominence_max_c": prom_max,
                "risk_line_c": risk_line,
            }

            if best is None:
                best = candidate
                continue

            # Choose the strongest (higher max), then more conservative (later end).
            if (candidate["max_temp"] > best["max_temp"] + 1e-6) or (
                abs(candidate["max_temp"] - best["max_temp"]) <= 1e-6
                and candidate["end_time"] > best["end_time"]
            ):
                best = candidate

        return best

    def _find_valid_night_risk_peak_legacy(
        self,
        series: List[Tuple[datetime, float]],
        tz_offset: float,
        day_ref_max: float,
    ) -> Optional[Dict[str, Any]]:
        """Find a night risk peak with persistence + local-prominence filters."""
        rebound_threshold = float(self.config.FORECAST_GUARD_PEAK_THRESHOLD_C)
        min_points = max(1, int(self.config.FORECAST_GUARD_PEAK_MIN_POINTS))
        min_prominence = max(0.0, float(self.config.FORECAST_GUARD_PEAK_PROMINENCE_C))
        risk_line = day_ref_max - rebound_threshold

        day_points: List[Tuple[datetime, float, float]] = []
        for dt_utc, temp in series:
            local_hour = self._local_hour(dt_utc, tz_offset)
            if 12 <= local_hour < 24:
                day_points.append((dt_utc, temp, local_hour))

        if len(day_points) < 3:
            return None

        for i in range(1, len(day_points) - 1):
            curr_dt, curr_temp, curr_hour = day_points[i]
            if curr_hour < 17 or curr_temp < risk_line:
                continue

            prev_temp = day_points[i - 1][1]
            next_temp = day_points[i + 1][1]

            # Local peak shape.
            if not (curr_temp > prev_temp and curr_temp >= next_temp):
                continue

            prominence = curr_temp - max(prev_temp, next_temp)
            if prominence + 1e-9 < min_prominence:
                continue

            # Persistence: count contiguous night points above risk line around this peak.
            left = i
            while (
                left - 1 >= 0
                and day_points[left - 1][2] >= 17
                and day_points[left - 1][1] >= risk_line
            ):
                left -= 1
            right = i
            while (
                right + 1 < len(day_points)
                and day_points[right + 1][2] >= 17
                and day_points[right + 1][1] >= risk_line
            ):
                right += 1

            duration = right - left + 1
            if duration < min_points:
                continue

            return {
                "time": curr_dt,
                "local_hour": curr_hour,
                "temp": curr_temp,
                "duration_points": duration,
                "prominence_c": prominence,
                "risk_line_c": risk_line,
            }

        return None

    def _extract_peaks(self, series: List[Tuple[datetime, float]], tz_offset: float) -> List[Dict[str, Any]]:
        """从预报序列中提取 12:00-24:00 的局部极值点 (波峰)"""
        # 1. 过滤 12 点之后的点
        day_points = [(dt, t) for dt, t in series if 12 <= self._local_hour(dt, tz_offset) < 24]
        if len(day_points) < 2:
            return []
            
        peaks = []
        # 局部极大值识别: T[i] > T[i-1] 且 T[i] >= T[i+1]
        for i in range(1, len(day_points) - 1):
            t_prev = day_points[i-1][1]
            t_curr = day_points[i][1]
            t_next = day_points[i+1][1]
            
            if t_curr > t_prev and t_curr >= t_next:
                peaks.append({
                    "time": day_points[i][0],
                    "local_hour": self._local_hour(day_points[i][0], tz_offset),
                    "temp": t_curr
                })
        
        # 边界补齐: 如果结尾一直在涨，最后一个点也视为潜在峰值
        if len(day_points) >= 2 and day_points[-1][1] > day_points[-2][1]:
            peaks.append({
                "time": day_points[-1][0],
                "local_hour": self._local_hour(day_points[-1][0], tz_offset),
                "temp": day_points[-1][1]
            })
            
        return peaks

    @staticmethod
    def _local_hour(dt_utc: datetime, offset: float) -> float:
        local = dt_utc + timedelta(hours=offset)
        return local.hour + local.minute / 60.0

    def _fetch_forecast_sources(self, lat: float, lon: float) -> Dict[str, List[Tuple[datetime, float]]]:
        sources: Dict[str, List[Tuple[datetime, float]]] = {}

        try:
            sources["ecmwf_ifs"] = self._fetch_open_meteo(lat, lon, "ecmwf_ifs")
        except Exception as exc:
            logger.debug("ecmwf_ifs fetch failed: %s", exc)

        try:
            sources["gfs_global"] = self._fetch_open_meteo(lat, lon, "gfs_global")
        except Exception as exc:
            logger.debug("gfs_global fetch failed: %s", exc)

        try:
            sources["met_no"] = self._fetch_met_no(lat, lon)
        except Exception as exc:
            logger.debug("met.no fetch failed: %s", exc)

        if self.config.METOFFICE_SITE_SPECIFIC_API_KEY:
            try:
                sources["metoffice_site_specific"] = self._fetch_metoffice_site_specific(lat, lon)
            except Exception as exc:
                logger.debug("metoffice site-specific fetch failed: %s", exc)

        return sources

    def _fetch_open_meteo(self, lat: float, lon: float, model: str) -> List[Tuple[datetime, float]]:
        resp = self.session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m",
                "timezone": "UTC",
                "forecast_days": 2,
                "models": model,
            },
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        times = body.get("hourly", {}).get("time", [])
        temps = body.get("hourly", {}).get("temperature_2m", [])
        out = []
        for t, v in zip(times, temps):
            if v is None:
                continue
            dt = datetime.fromisoformat(f"{t}:00+00:00").astimezone(timezone.utc)
            out.append((dt, float(v)))
        return out

    def _fetch_met_no(self, lat: float, lon: float) -> List[Tuple[datetime, float]]:
        resp = self.session.get(
            f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}",
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        ts = body.get("properties", {}).get("timeseries", [])
        out = []
        for item in ts:
            t = item.get("time")
            v = (
                item.get("data", {})
                .get("instant", {})
                .get("details", {})
                .get("air_temperature")
            )
            if not t or v is None:
                continue
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)
            out.append((dt, float(v)))
        return out

    def _fetch_metoffice_site_specific(self, lat: float, lon: float) -> List[Tuple[datetime, float]]:
        url = (
            f"{self.config.METOFFICE_SITE_SPECIFIC_BASE_URL}"
            f"{self.config.METOFFICE_SITE_SPECIFIC_CONTEXT}/point/hourly"
        )
        resp = self.session.get(
            url,
            headers={"apikey": self.config.METOFFICE_SITE_SPECIFIC_API_KEY, "accept": "application/json"},
            params={"latitude": lat, "longitude": lon},
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        features = body.get("features", [])
        if not features:
            return []
        series = features[0].get("properties", {}).get("timeSeries", [])
        out = []
        for point in series:
            t = point.get("time")
            v = point.get("screenTemperature")
            if not t or v is None:
                continue
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)
            out.append((dt, float(v)))
        return out
