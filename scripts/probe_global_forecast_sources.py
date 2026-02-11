#!/usr/bin/env python3
"""
Probe global forecast sources for all configured locations.

Sources:
1) ECMWF IFS (via Open-Meteo model: ecmwf_ifs)
2) NOAA GFS (via Open-Meteo model: gfs_global)
3) MET Norway Locationforecast (direct API)

Outputs:
- Console summary
- JSON report under data/research/
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[1]
LOCATIONS_FILE = ROOT / "locations.json"
OUT_DIR = ROOT / "data" / "research"
REQUEST_TIMEOUT = 25


@dataclass
class ProbeResult:
    location: str
    source: str
    status: str
    points: int
    start_time: Optional[str]
    end_time: Optional[str]
    horizon_hours: Optional[int]
    interval_hours: List[int]
    sample_temp_c: Optional[float]
    note: str = ""


def load_locations(path: Path) -> Dict[str, Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _parse_time(ts: str) -> datetime:
    # Open-Meteo uses "YYYY-MM-DDTHH:MM"; met.no uses "...Z".
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def analyze_timeseries(times: List[str], temps: List[Optional[float]]) -> Tuple[int, Optional[str], Optional[str], Optional[int], List[int], Optional[float]]:
    valid_pairs = [(t, temp) for t, temp in zip(times, temps) if temp is not None]
    if not valid_pairs:
        return 0, None, None, None, [], None

    parsed_times = [_parse_time(t) for t, _ in valid_pairs]
    intervals = []
    for idx in range(len(parsed_times) - 1):
        delta_h = int((parsed_times[idx + 1] - parsed_times[idx]).total_seconds() // 3600)
        intervals.append(delta_h)

    unique_intervals = sorted(set(intervals))
    horizon = int((parsed_times[-1] - parsed_times[0]).total_seconds() // 3600)

    return (
        len(valid_pairs),
        valid_pairs[0][0],
        valid_pairs[-1][0],
        horizon,
        unique_intervals,
        valid_pairs[0][1],
    )


def probe_open_meteo_model(location: str, lat: float, lon: float, model: str) -> ProbeResult:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m",
        "timezone": "UTC",
        "forecast_days": 16,
        "models": model,
    }
    source_name = f"open-meteo:{model}"
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        times = body.get("hourly", {}).get("time", [])
        temps = body.get("hourly", {}).get("temperature_2m", [])
        points, start, end, horizon, intervals, sample_temp = analyze_timeseries(times, temps)
        return ProbeResult(
            location=location,
            source=source_name,
            status="ok",
            points=points,
            start_time=start,
            end_time=end,
            horizon_hours=horizon,
            interval_hours=intervals,
            sample_temp_c=sample_temp,
        )
    except Exception as exc:
        return ProbeResult(
            location=location,
            source=source_name,
            status="error",
            points=0,
            start_time=None,
            end_time=None,
            horizon_hours=None,
            interval_hours=[],
            sample_temp_c=None,
            note=str(exc),
        )


def probe_met_no(location: str, lat: float, lon: float) -> ProbeResult:
    url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
    headers = {
        "User-Agent": "WeatherBotProbe/1.0 (dev@example.com)"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        series = body.get("properties", {}).get("timeseries", [])
        times = []
        temps = []
        for item in series:
            times.append(item.get("time"))
            temps.append(
                item.get("data", {})
                .get("instant", {})
                .get("details", {})
                .get("air_temperature")
            )
        points, start, end, horizon, intervals, sample_temp = analyze_timeseries(times, temps)
        return ProbeResult(
            location=location,
            source="met.no:locationforecast",
            status="ok",
            points=points,
            start_time=start,
            end_time=end,
            horizon_hours=horizon,
            interval_hours=intervals,
            sample_temp_c=sample_temp,
        )
    except Exception as exc:
        return ProbeResult(
            location=location,
            source="met.no:locationforecast",
            status="error",
            points=0,
            start_time=None,
            end_time=None,
            horizon_hours=None,
            interval_hours=[],
            sample_temp_c=None,
            note=str(exc),
        )


def run_probe() -> Dict[str, Any]:
    locations = load_locations(LOCATIONS_FILE)
    rows: List[ProbeResult] = []

    for location, cfg in locations.items():
        lat = float(cfg["lat"])
        lon = float(cfg["lon"])
        rows.append(probe_open_meteo_model(location, lat, lon, "ecmwf_ifs"))
        rows.append(probe_open_meteo_model(location, lat, lon, "gfs_global"))
        rows.append(probe_met_no(location, lat, lon))

    report = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "locations_file": str(LOCATIONS_FILE),
        "results": [asdict(r) for r in rows],
    }
    return report


def print_summary(report: Dict[str, Any]) -> None:
    print(f"Generated: {report['generated_at']}")
    print("location | source | status | points | horizon_h | intervals_h | start -> end")
    print("-" * 120)
    for r in report["results"]:
        intervals = ",".join(str(v) for v in r["interval_hours"]) if r["interval_hours"] else "-"
        horizon = r["horizon_hours"] if r["horizon_hours"] is not None else "-"
        start = r["start_time"] or "-"
        end = r["end_time"] or "-"
        print(
            f"{r['location']:7} | {r['source']:24} | {r['status']:5} | "
            f"{r['points']:6} | {horizon:9} | {intervals:11} | {start} -> {end}"
        )
        if r.get("note"):
            print(f"  note: {r['note']}")


def save_report(report: Dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"forecast_source_probe_{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path


def main() -> None:
    report = run_probe()
    print_summary(report)
    out_path = save_report(report)
    print(f"\nSaved report: {out_path}")


if __name__ == "__main__":
    main()
