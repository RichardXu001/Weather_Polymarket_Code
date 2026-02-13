#!/usr/bin/env python3
"""
Offline backtest for ForecastGuard "night risk window" logic using recorded CSVs.

Inputs:
- forecast_raw_{preset}_{YYYYMMDD}_*.csv (corrected forecast curves per source)
- weather_recording_{preset}_{YYYYMMDD}_*.csv (NOAA/aux measurements + FG logs)

This script does NOT call any network APIs. It replays the risk detection portion only
and compares legacy "first peak" vs new "risk window" behavior.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys

# Allow running from repo root without installing as a package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.config import QuantConfig
from engine.forecast_guard import ForecastGuardManager


DT_SYS_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_dt_sys(s: str) -> datetime:
    return datetime.strptime(s, DT_SYS_FMT)


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


@dataclass(frozen=True)
class WeatherRow:
    ts_system: datetime  # server/system-local timestamp from CSV "timestamp"
    noaa_curr: float


def load_weather_rows(paths: List[str]) -> List[WeatherRow]:
    rows: List[WeatherRow] = []
    for p in paths:
        with open(p, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                ts = _parse_dt_sys(row["timestamp"])
                noaa = _safe_float(row.get("noaa_curr"))
                if noaa is None:
                    continue
                rows.append(WeatherRow(ts_system=ts, noaa_curr=noaa))
    rows.sort(key=lambda x: x.ts_system)
    # De-dupe exact duplicates (common when merging multiple sessions).
    dedup: List[WeatherRow] = []
    last_key: Optional[Tuple[datetime, float]] = None
    for x in rows:
        key = (x.ts_system, x.noaa_curr)
        if key == last_key:
            continue
        dedup.append(x)
        last_key = key
    return dedup


def _nearest_weather(rows: List[WeatherRow], ts_system: datetime) -> Optional[int]:
    if not rows:
        return None
    lo, hi = 0, len(rows) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid].ts_system < ts_system:
            lo = mid + 1
        else:
            hi = mid
    # lo is first >= ts; pick closer of lo and lo-1
    cand = [lo]
    if lo - 1 >= 0:
        cand.append(lo - 1)
    best = min(cand, key=lambda i: abs((rows[i].ts_system - ts_system).total_seconds()))
    return best


@dataclass
class ForecastSnapshot:
    ts_system: datetime
    ts_utc: datetime
    ts_local: datetime
    rows_by_source: Dict[str, Dict[str, str]]


def load_forecast_snapshots(paths: List[str]) -> List[ForecastSnapshot]:
    by_ts: Dict[str, Dict[str, Any]] = {}
    for p in paths:
        with open(p, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                k = row["timestamp_system"]
                entry = by_ts.setdefault(
                    k,
                    {
                        "timestamp_system": row["timestamp_system"],
                        "timestamp_utc": row["timestamp_utc"],
                        "timestamp_local": row["timestamp_local"],
                        "rows_by_source": {},
                    },
                )
                entry["rows_by_source"][row["source"]] = row

    snaps: List[ForecastSnapshot] = []
    for k, entry in by_ts.items():
        ts_system = _parse_dt_sys(entry["timestamp_system"])
        ts_utc = _parse_dt_sys(entry["timestamp_utc"]).replace(tzinfo=timezone.utc)
        ts_local = _parse_dt_sys(entry["timestamp_local"])
        snaps.append(
            ForecastSnapshot(
                ts_system=ts_system,
                ts_utc=ts_utc,
                ts_local=ts_local,
                rows_by_source=dict(entry["rows_by_source"]),
            )
        )
    snaps.sort(key=lambda x: x.ts_system)
    return snaps


def build_hourly_series_from_row(
    row: Dict[str, str], tz_offset: float, snapshot_local: datetime
) -> List[Tuple[datetime, float]]:
    # forecast_raw already contains bias-corrected temps written by ForecastGuard.
    base_date = snapshot_local.date()
    out: List[Tuple[datetime, float]] = []
    for h in range(12, 25):
        col = f"Local_{h}h"
        t = _safe_float(row.get(col))
        if t is None:
            continue
        if h == 24:
            dt_local = datetime(base_date.year, base_date.month, base_date.day, 0, 0, 0) + timedelta(days=1)
        else:
            dt_local = datetime(base_date.year, base_date.month, base_date.day, h, 0, 0)
        dt_utc = (dt_local - timedelta(hours=tz_offset)).replace(tzinfo=timezone.utc)
        out.append((dt_utc, float(t)))
    out.sort(key=lambda x: x[0])
    return out


def local_hour(dt_utc: datetime, tz_offset: float) -> float:
    loc = dt_utc + timedelta(hours=tz_offset)
    return loc.hour + loc.minute / 60.0


def afternoon_forecast_max(series: List[Tuple[datetime, float]], tz_offset: float) -> Optional[float]:
    vals = [t for dt, t in series if 12 <= local_hour(dt, tz_offset) < 17]
    return max(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", required=True, help="e.g. ankara/seoul/london/nyc")
    ap.add_argument("--date", required=True, help="YYYYMMDD, e.g. 20260212")
    ap.add_argument(
        "--dir",
        default="data/server_pull",
        help="base directory that contains pulled recordings",
    )
    ap.add_argument(
        "--out",
        default="backtest_reports/fg_window_report.md",
        help="markdown report output path",
    )
    args = ap.parse_args()

    preset = args.preset
    ymd = args.date
    base_dir = Path(args.dir)
    target_date = datetime.strptime(ymd, "%Y%m%d").date()

    # tz_offset from locations.json
    with open("locations.json") as f:
        locs = json.load(f)
    if preset not in locs:
        raise SystemExit(f"Unknown preset {preset!r} in locations.json")
    tz_offset = float(locs[preset]["tz_offset"])

    # Discover inputs
    forecast_paths = sorted(
        glob.glob(str(base_dir / f"{preset}_{ymd}*" / "recordings" / f"forecast_raw_{preset}_{ymd}_*.csv"))
        + glob.glob(str(base_dir / f"{preset}_{ymd}*" / f"forecast_raw_{preset}_{ymd}_*.csv"))
        + glob.glob(str(base_dir / "**" / f"forecast_raw_{preset}_{ymd}_*.csv"), recursive=True)
    )
    weather_paths = sorted(
        glob.glob(str(base_dir / f"{preset}_{ymd}*" / "recordings" / f"weather_recording_{preset}_{ymd}_*.csv"))
        + glob.glob(str(base_dir / f"{preset}_{ymd}*" / f"weather_recording_{preset}_{ymd}_*.csv"))
        + glob.glob(str(base_dir / "**" / f"weather_recording_{preset}_{ymd}_*.csv"), recursive=True)
    )

    forecast_paths = sorted(set(forecast_paths))
    weather_paths = sorted(set(weather_paths))

    if not forecast_paths:
        raise SystemExit("No forecast_raw inputs found")
    if not weather_paths:
        raise SystemExit("No weather_recording inputs found")

    weather_rows = load_weather_rows(weather_paths)
    snaps = load_forecast_snapshots(forecast_paths)
    if not snaps:
        raise SystemExit("No forecast snapshots parsed")

    # Derive server-local vs location-local delta hours from recordings.
    # forecast_raw stores both timestamp_system (server) and timestamp_local (location).
    delta_sys_minus_local_hours = (snaps[0].ts_system - snaps[0].ts_local).total_seconds() / 3600.0

    # Keep only snapshots for the target *location local date*.
    snaps = [s for s in snaps if s.ts_local.date() == target_date]

    # Filter weather rows to the same location local date as well.
    # weather_recording only stores server-local timestamp, so we project it to location-local time using the same delta.
    weather_rows = [
        r
        for r in weather_rows
        if (r.ts_system - timedelta(hours=delta_sys_minus_local_hours)).date() == target_date
    ]

    cfg = QuantConfig()
    fg = ForecastGuardManager(cfg)

    # Precompute cumulative NOAA max (proxy for state.max_temp_overall) on this local date only.
    cum_max: List[float] = []
    m = float("-inf")
    for r in weather_rows:
        if r.noaa_curr > m:
            m = r.noaa_curr
        cum_max.append(m)

    # Replay snapshots
    diffs_peak_time = 0
    diffs_anchor_time = 0
    risk_count_changed = 0

    rows_md: List[str] = []
    rows_md.append(f"# ForecastGuard Window Backtest: {preset.upper()} {ymd}")
    rows_md.append("")
    rows_md.append(f"- Inputs: {len(weather_paths)} weather recordings, {len(forecast_paths)} forecast_raw recordings")
    rows_md.append(f"- Merged: {len(weather_rows)} weather rows, {len(snaps)} forecast snapshots")
    rows_md.append(f"- TZ offset: {tz_offset}")
    rows_md.append("")
    rows_md.append("## Snapshot Comparison (Legacy peak vs Window)")
    rows_md.append("")
    rows_md.append("| snapshot(system) | noaa | day_max | src | legacy_peak | window | window_anchor |")
    rows_md.append("|---|---:|---:|---|---|---|---|")

    # Limit verbose table to risky-related snapshots to keep report readable.
    for s in snaps:
        wi = _nearest_weather(weather_rows, s.ts_system)
        if wi is None:
            continue
        noaa = weather_rows[wi].noaa_curr
        day_max = cum_max[wi]

        legacy_risky = 0
        window_risky = 0

        per_src_rows: List[Tuple[str, str, str, str]] = []
        for src, row in sorted(s.rows_by_source.items()):
            series = build_hourly_series_from_row(row, tz_offset, s.ts_local)
            if not series:
                continue

            amax = afternoon_forecast_max(series, tz_offset)
            if amax is None:
                continue
            day_ref_max = max(float(amax), float(day_max))

            legacy = fg._find_valid_night_risk_peak_legacy(series, tz_offset, day_ref_max)
            window = fg._find_valid_night_risk_window(series, tz_offset, day_ref_max)

            legacy_desc = "-"
            if legacy is not None:
                legacy_risky += 1
                legacy_desc = f"{legacy['local_hour']:.1f}h/{legacy['temp']:.1f}C dur={legacy['duration_points']} prom={legacy['prominence_c']:.2f}"

            window_desc = "-"
            anchor_desc = "-"
            if window is not None:
                window_risky += 1
                window_desc = (
                    f"{window['start_local_hour']:.1f}-{window['end_local_hour']:.1f}h "
                    f"max={window['max_temp']:.1f}C@{window['max_local_hour']:.1f}h "
                    f"dur={window['duration_points']} prom_max={window['prominence_max_c']:.2f}"
                )
                # Anchor uses window end (conservative unlock reference)
                anchor_desc = f"end@{window['end_local_hour']:.1f}h"

            # Diff stats (only when both exist)
            if legacy is not None and window is not None:
                if abs(legacy["local_hour"] - window["max_local_hour"]) > 1e-6:
                    diffs_peak_time += 1
                if abs(legacy["local_hour"] - window["end_local_hour"]) > 1e-6:
                    diffs_anchor_time += 1

            per_src_rows.append((src, legacy_desc, window_desc, anchor_desc))

        if legacy_risky != window_risky:
            risk_count_changed += 1

        # Only emit table rows if either version sees >=1 risky source, or risk_count changed.
        if max(legacy_risky, window_risky) == 0 and legacy_risky == window_risky:
            continue

        for src, legacy_desc, window_desc, anchor_desc in per_src_rows:
            rows_md.append(
                f"| {s.ts_system.strftime(DT_SYS_FMT)} | {noaa:.1f} | {day_max:.1f} | {src} | {legacy_desc} | {window_desc} | {anchor_desc} |"
            )

    rows_md.append("")
    rows_md.append("## Summary")
    rows_md.append("")
    rows_md.append(f"- Peak time changed (legacy_peak_hour != window.max_hour): {diffs_peak_time}")
    rows_md.append(f"- Anchor time changed (legacy_peak_hour != window.end_hour): {diffs_anchor_time}")
    rows_md.append(f"- Risky source count changed at snapshot-level: {risk_count_changed}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(rows_md) + "\n", encoding="utf-8")

    print(f"[OK] wrote report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
