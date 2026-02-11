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
        if state.noaa_curr is None:
            result["locked"] = bool(self.config.FORECAST_GUARD_FAIL_SAFE)
            result["reason"] = "No NOAA anchor"
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
            },
        )

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
        threshold = max(1, int(self.config.FORECAST_GUARD_RISK_SOURCE_THRESHOLD))
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

            # 2. 识别未来的风险波峰 (当天 17:00 后的波峰)
            night_peaks = [p for p in peaks if p["local_hour"] >= 17]
            
            # 3. 核心判定：统一拦截阈值 (1.5C)
            rebound_threshold = self.config.FORECAST_GUARD_PEAK_THRESHOLD_C
            
            c_night_peak = False
            risk_desc = "OK"
            night_peak_dt = None
            
            for np in night_peaks:
                # 核心拦截逻辑：晚上峰值距离日间基准不足 [1.5C] 或 已经反超
                if np["temp"] >= (day_ref_max - rebound_threshold):
                    c_night_peak = True
                    night_peak_dt = np["time"]
                    risk_desc = f"夜间峰值风险[{np['local_hour']:.1f}h/{np['temp']:.1f}C]"
                    break

            risky = c_night_peak
            if risky:
                risk_count += 1
                if night_peak_dt is not None:
                    if latest_risky_peak is None or night_peak_dt > latest_risky_peak:
                        latest_risky_peak = night_peak_dt

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
                "night_peak_c": round(night_peaks[0]["temp"], 3) if night_peaks else None,
                "night_peak_time_utc": night_peak_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if night_peak_dt else None,
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
