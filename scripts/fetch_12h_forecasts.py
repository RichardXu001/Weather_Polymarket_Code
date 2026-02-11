#!/usr/bin/env python3
"""
Fetch next 12 forecast points for all locations from:
- ECMWF IFS (via Open-Meteo)
- NOAA GFS (via Open-Meteo)
- MET Norway Locationforecast

Output JSON is written to data/research/.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
LOCATIONS_FILE = ROOT / "locations.json"
OUT_DIR = ROOT / "data" / "research"
TIMEOUT = 25

load_dotenv(ROOT / ".env")


def parse_utc(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    if len(ts) == 16:
        ts = ts + ":00+00:00"
    elif len(ts) == 19 and "+" not in ts:
        ts = ts + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_local(dt_utc: datetime, tz_offset: float) -> str:
    local_dt = dt_utc + timedelta(hours=tz_offset)
    return local_dt.strftime("%Y-%m-%d %H:%M")


def next_n_points(
    times: List[str],
    temps: List[Optional[float]],
    now_utc: datetime,
    n: int,
    tz_offset: float,
) -> List[Dict[str, Any]]:
    pts: List[Tuple[datetime, float]] = []
    for t, v in zip(times, temps):
        if v is None:
            continue
        dt_utc = parse_utc(t).astimezone(timezone.utc)
        if dt_utc >= now_utc:
            pts.append((dt_utc, float(v)))
    pts = pts[:n]
    return [
        {
            "time_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_local": format_local(dt, tz_offset),
            "temp_c": v,
        }
        for dt, v in pts
    ]


def celsius_from_unit(temp: float, unit: str) -> float:
    if unit.upper() == "F":
        return (temp - 32.0) / 1.8
    return temp


def fetch_open_meteo(lat: float, lon: float, model: str) -> Dict[str, Any]:
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "timezone": "UTC",
            "forecast_days": 2,
            "models": model,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_met_no(lat: float, lon: float) -> Dict[str, Any]:
    r = requests.get(
        f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}",
        headers={"User-Agent": "WeatherBotProbe/1.0 (dev@example.com)"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_metoffice_site_specific(lat: float, lon: float, api_key: str, base_url: str, context: str) -> Dict[str, Any]:
    url = f"{base_url}{context}/point/hourly"
    r = requests.get(
        url,
        headers={"apikey": api_key, "accept": "application/json"},
        params={"latitude": lat, "longitude": lon},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_nws_hourly(lat: float, lon: float) -> Dict[str, Any]:
    points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    headers = {"User-Agent": "WeatherBotProbe/1.0 (dev@example.com)"}
    points = requests.get(points_url, headers=headers, timeout=TIMEOUT)
    points.raise_for_status()
    points_body = points.json()
    hourly_url = points_body.get("properties", {}).get("forecastHourly")
    if not hourly_url:
        raise ValueError("NWS points response missing forecastHourly")
    hourly = requests.get(hourly_url, headers=headers, timeout=TIMEOUT)
    hourly.raise_for_status()
    return hourly.json()


def nearest_metoffice_site(lat: float, lon: float, api_key: str) -> str:
    url = "https://datapoint.metoffice.gov.uk/public/data/val/wxfcs/all/json/sitelist"
    resp = requests.get(url, params={"key": api_key}, timeout=TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    locations = body.get("Locations", {}).get("Location", [])
    if not locations:
        raise ValueError("Met Office sitelist empty")

    best = None
    best_d = float("inf")
    for site in locations:
        try:
            s_lat = float(site["latitude"])
            s_lon = float(site["longitude"])
            d = (s_lat - lat) ** 2 + (s_lon - lon) ** 2
            if d < best_d:
                best_d = d
                best = site
        except Exception:
            continue
    if not best or "id" not in best:
        raise ValueError("Cannot find nearest Met Office site")
    return str(best["id"])


def fetch_metoffice_datapoint(site_id: str, api_key: str) -> Dict[str, Any]:
    url = f"https://datapoint.metoffice.gov.uk/public/data/val/wxfcs/all/json/{site_id}"
    resp = requests.get(url, params={"res": "3hourly", "key": api_key}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _kma_grid_from_latlon(lat: float, lon: float) -> Tuple[int, int]:
    # KMA DFS grid conversion (Lambert Conformal Conic).
    re = 6371.00877
    grid = 5.0
    slat1 = 30.0
    slat2 = 60.0
    olon = 126.0
    olat = 38.0
    xo = 43.0
    yo = 136.0

    deg2rad = math.pi / 180.0
    re = re / grid
    slat1 *= deg2rad
    slat2 *= deg2rad
    olon *= deg2rad
    olat *= deg2rad

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn * math.cos(slat1)) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * deg2rad * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lon * deg2rad - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = int(ra * math.sin(theta) + xo + 0.5)
    y = int(ro - ra * math.cos(theta) + yo + 0.5)
    return x, y


def _latest_kma_vilage_base(now_kst: datetime) -> Tuple[str, str]:
    # Official cycle times for VilageFcst
    cycles = ["0200", "0500", "0800", "1100", "1400", "1700", "2000", "2300"]
    today = now_kst.strftime("%Y%m%d")
    hhmm = now_kst.strftime("%H%M")
    eligible = [c for c in cycles if c <= hhmm]
    if eligible:
        return today, eligible[-1]
    yesterday = (now_kst - timedelta(days=1)).strftime("%Y%m%d")
    return yesterday, "2300"


def _latest_kma_ultra_base(now_kst: datetime) -> Tuple[str, str]:
    # getUltraSrtFcst base_time runs each hour at :30 (available after ~:45).
    candidate = now_kst - timedelta(hours=1)
    return candidate.strftime("%Y%m%d"), candidate.strftime("%H30")


def fetch_kma_ultra_short(service_key: str, nx: int, ny: int, base_date: str, base_time: str) -> Dict[str, Any]:
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"
    params = {
        "serviceKey": service_key,
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": str(nx),
        "ny": str(ny),
    }
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_kma_vilage(service_key: str, nx: int, ny: int, base_date: str, base_time: str) -> Dict[str, Any]:
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": service_key,
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": str(nx),
        "ny": str(ny),
    }
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def kma_items_to_points(
    items: List[Dict[str, Any]],
    now_utc: datetime,
    n: int,
    tz_offset: float,
    temp_key: str,
) -> List[Dict[str, Any]]:
    by_dt: Dict[str, float] = {}
    for it in items:
        if it.get("category") != temp_key:
            continue
        d = it.get("fcstDate")
        t = it.get("fcstTime")
        val = it.get("fcstValue")
        if not (d and t and val is not None):
            continue
        key = f"{d}{t}"
        try:
            by_dt[key] = float(val)
        except Exception:
            continue

    points: List[Tuple[datetime, float]] = []
    for k, v in by_dt.items():
        dt_kst = datetime.strptime(k, "%Y%m%d%H%M").replace(tzinfo=timezone(timedelta(hours=9)))
        dt_utc = dt_kst.astimezone(timezone.utc)
        if dt_utc >= now_utc:
            points.append((dt_utc, v))
    points.sort(key=lambda x: x[0])
    points = points[:n]
    return [
        {
            "time_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_local": format_local(dt, tz_offset),
            "temp_c": temp,
        }
        for dt, temp in points
    ]


def load_locations() -> Dict[str, Any]:
    with LOCATIONS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def run(hours: int = 12) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    locations = load_locations()
    out: Dict[str, Any] = {
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_points": hours,
        "locations": {},
    }

    for name, cfg in locations.items():
        lat = float(cfg["lat"])
        lon = float(cfg["lon"])
        tz_offset = float(cfg.get("tz_offset", 0))

        city: Dict[str, Any] = {
            "lat": lat,
            "lon": lon,
            "tz_offset": tz_offset,
            "sources": {},
        }

        # ECMWF IFS via Open-Meteo
        try:
            body = fetch_open_meteo(lat, lon, "ecmwf_ifs")
            times = body.get("hourly", {}).get("time", [])
            temps = body.get("hourly", {}).get("temperature_2m", [])
            city["sources"]["ecmwf_ifs"] = next_n_points(times, temps, now_utc, hours, tz_offset)
        except Exception as exc:
            city["sources"]["ecmwf_ifs"] = {"error": str(exc)}

        # NOAA GFS via Open-Meteo
        try:
            body = fetch_open_meteo(lat, lon, "gfs_global")
            times = body.get("hourly", {}).get("time", [])
            temps = body.get("hourly", {}).get("temperature_2m", [])
            city["sources"]["gfs_global"] = next_n_points(times, temps, now_utc, hours, tz_offset)
        except Exception as exc:
            city["sources"]["gfs_global"] = {"error": str(exc)}

        # MET Norway
        try:
            body = fetch_met_no(lat, lon)
            ts = body.get("properties", {}).get("timeseries", [])
            times = [x.get("time") for x in ts]
            temps = [
                x.get("data", {})
                .get("instant", {})
                .get("details", {})
                .get("air_temperature")
                for x in ts
            ]
            city["sources"]["met_no"] = next_n_points(times, temps, now_utc, hours, tz_offset)
        except Exception as exc:
            city["sources"]["met_no"] = {"error": str(exc)}

        # Met Office site-specific (global point hourly)
        mo_site_key = os.getenv("METOFFICE_SITE_SPECIFIC_API_KEY", "").strip()
        mo_site_base = os.getenv("METOFFICE_SITE_SPECIFIC_BASE_URL", "https://gateway.api-management.metoffice.cloud")
        mo_site_ctx = os.getenv("METOFFICE_SITE_SPECIFIC_CONTEXT", "/sitespecific/v0")
        if mo_site_key:
            try:
                body = fetch_metoffice_site_specific(lat, lon, mo_site_key, mo_site_base, mo_site_ctx)
                features = body.get("features", [])
                series = []
                if features:
                    series = features[0].get("properties", {}).get("timeSeries", [])
                times = [x.get("time") for x in series]
                temps = [x.get("screenTemperature") for x in series]
                city["sources"]["metoffice_site_specific"] = next_n_points(times, temps, now_utc, hours, tz_offset)
            except Exception as exc:
                city["sources"]["metoffice_site_specific"] = {"error": str(exc)}
        else:
            city["sources"]["metoffice_site_specific"] = {"error": "Missing METOFFICE_SITE_SPECIFIC_API_KEY"}

        # NYC local source: NWS hourly forecast
        if name == "nyc":
            try:
                body = fetch_nws_hourly(lat, lon)
                periods = body.get("properties", {}).get("periods", [])
                times = []
                temps = []
                for p in periods:
                    times.append(p.get("startTime"))
                    temp_v = p.get("temperature")
                    temp_u = p.get("temperatureUnit", "F")
                    temps.append(celsius_from_unit(float(temp_v), temp_u) if temp_v is not None else None)
                city["sources"]["nws_hourly"] = next_n_points(times, temps, now_utc, hours, tz_offset)
            except Exception as exc:
                city["sources"]["nws_hourly"] = {"error": str(exc)}

        # London local source: Met Office DataPoint (3-hourly, free with API key)
        if name == "london":
            met_key = os.getenv("METOFFICE_DATAPOINT_API_KEY", "").strip()
            if not met_key:
                city["sources"]["metoffice_datapoint_3h"] = {
                    "error": "Missing METOFFICE_DATAPOINT_API_KEY"
                }
            else:
                try:
                    site_id = os.getenv("METOFFICE_SITE_ID_LONDON", "").strip()
                    if not site_id:
                        site_id = nearest_metoffice_site(lat, lon, met_key)
                    body = fetch_metoffice_datapoint(site_id, met_key)
                    reps = (
                        body.get("SiteRep", {})
                        .get("DV", {})
                        .get("Location", {})
                        .get("Period", [])
                    )
                    times: List[str] = []
                    temps: List[Optional[float]] = []
                    for period in reps:
                        period_day = period.get("value", "")[:10]
                        for rep in period.get("Rep", []):
                            mins = int(rep.get("$", "0"))
                            dt = datetime.fromisoformat(period_day).replace(tzinfo=timezone.utc) + timedelta(minutes=mins)
                            times.append(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
                            t = rep.get("T")
                            temps.append(float(t) if t is not None else None)
                    city["sources"]["metoffice_datapoint_3h"] = next_n_points(
                        times, temps, now_utc, hours, tz_offset
                    )
                except Exception as exc:
                    city["sources"]["metoffice_datapoint_3h"] = {"error": str(exc)}

        # Seoul local source: KMA OpenAPI
        if name == "seoul":
            kma_key = os.getenv("KMA_SERVICE_KEY", "").strip()
            if not kma_key:
                city["sources"]["kma_openapi"] = {
                    "error": "Missing KMA_SERVICE_KEY"
                }
            else:
                try:
                    nx_env = os.getenv("KMA_NX_SEOUL", "").strip()
                    ny_env = os.getenv("KMA_NY_SEOUL", "").strip()
                    if nx_env and ny_env:
                        nx = int(nx_env)
                        ny = int(ny_env)
                    else:
                        nx, ny = _kma_grid_from_latlon(lat, lon)

                    now_kst = now_utc.astimezone(timezone(timedelta(hours=9)))
                    ultra_date, ultra_time = _latest_kma_ultra_base(now_kst)
                    vil_date, vil_time = _latest_kma_vilage_base(now_kst)

                    ultra = fetch_kma_ultra_short(kma_key, nx, ny, ultra_date, ultra_time)
                    ultra_items = (
                        ultra.get("response", {})
                        .get("body", {})
                        .get("items", {})
                        .get("item", [])
                    )
                    ultra_points = kma_items_to_points(ultra_items, now_utc, 6, tz_offset, temp_key="T1H")

                    village = fetch_kma_vilage(kma_key, nx, ny, vil_date, vil_time)
                    village_items = (
                        village.get("response", {})
                        .get("body", {})
                        .get("items", {})
                        .get("item", [])
                    )
                    # Fill the remainder up to requested points from TMP (short-term forecast).
                    remain = max(hours - len(ultra_points), 0)
                    village_points = kma_items_to_points(village_items, now_utc, max(remain + 6, 12), tz_offset, temp_key="TMP")

                    merged: List[Dict[str, Any]] = []
                    seen = set()
                    for pt in ultra_points + village_points:
                        if pt["time_utc"] in seen:
                            continue
                        seen.add(pt["time_utc"])
                        merged.append(pt)
                    merged.sort(key=lambda x: x["time_utc"])
                    city["sources"]["kma_openapi"] = merged[:hours]
                except Exception as exc:
                    city["sources"]["kma_openapi"] = {"error": str(exc)}

        out["locations"][name] = city

    return out


def save_report(report: Dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"forecast_12h_{ts}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


def main() -> None:
    report = run(hours=12)
    path = save_report(report)
    print(f"saved: {path}")
    print(f"generated_at_utc: {report['generated_at_utc']}")
    for loc, payload in report["locations"].items():
        e = payload["sources"].get("ecmwf_ifs", [])
        g = payload["sources"].get("gfs_global", [])
        m = payload["sources"].get("met_no", [])
        n = payload["sources"].get("nws_hourly", [])
        mo = payload["sources"].get("metoffice_datapoint_3h", [])
        mos = payload["sources"].get("metoffice_site_specific", [])
        kma = payload["sources"].get("kma_openapi", [])
        e_n = len(e) if isinstance(e, list) else 0
        g_n = len(g) if isinstance(g, list) else 0
        m_n = len(m) if isinstance(m, list) else 0
        n_n = len(n) if isinstance(n, list) else 0
        mo_n = len(mo) if isinstance(mo, list) else 0
        mos_n = len(mos) if isinstance(mos, list) else 0
        kma_n = len(kma) if isinstance(kma, list) else 0
        print(
            f"{loc}: ecmwf={e_n}, gfs={g_n}, met_no={m_n}, "
            f"nws={n_n}, metoffice_datapoint={mo_n}, metoffice_site={mos_n}, kma={kma_n}"
        )


if __name__ == "__main__":
    main()
