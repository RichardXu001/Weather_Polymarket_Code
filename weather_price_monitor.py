import requests
import re
import time
import json
import csv
import os
import argparse
import sys
from datetime import datetime, timedelta

# 加载预设配置
def load_presets(json_path="locations.json"):
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

PRESETS = load_presets()

class WeatherPriceMonitor:
    def __init__(self, icao_code, event_slug, lat, lon, no_tty=False):
        self.icao_code = icao_code
        self.event_slug = event_slug
        self.lat = lat
        self.lon = lon
        self.no_tty = no_tty
        
        # API URLs
        self.metar_url = f"https://www.aviationweather.gov/api/data/metar?ids={icao_code}&format=json"
        self.open_meteo_url = f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}&current_weather=true&hourly=temperature_2m"
        self.met_no_url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={self.lat}&lon={self.lon}"
        self.poly_url = f"https://gamma-api.polymarket.com/events?slug={event_slug}"
        
        # CSV Setup
        start_time_str = datetime.now().strftime('%Y%m%d_%H%M')
        self.csv_file = f"weather_edge_{icao_code}_{start_time_str}.csv"
        self.columns = []

    def fetch_noaa(self):
        """Source 1: NOAA/METAR (Real-time Observation ONLY)"""
        try:
            r = requests.get(self.metar_url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data:
                    raw = data[0].get('rawOb', '')
                    match = re.search(r'\s(M?\d{2})/(M?\d{2})\s', raw)
                    if match:
                        t = match.group(1)
                        return -float(t[1:]) if t.startswith('M') else float(t)
        except: pass
        return None

    def fetch_open_meteo(self):
        """Source 2: Open-Meteo (Current + Forecast)"""
        try:
            r = requests.get(self.open_meteo_url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                curr = data.get('current_weather', {}).get('temperature')
                hourly = data.get('hourly', {}).get('temperature_2m', [])
                forecast_1h = hourly[1] if len(hourly) > 1 else None
                return curr, forecast_1h
        except: pass
        return None, None

    def fetch_met_no(self):
        """Source 3: Met.no (Current + Forecast)"""
        try:
            headers = {'User-Agent': 'WeatherMonitorBot/1.0'}
            r = requests.get(self.met_no_url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                timeseries = data.get('properties', {}).get('timeseries', [])
                if timeseries:
                    curr = timeseries[0].get('data', {}).get('instant', {}).get('details', {}).get('air_temperature')
                    forecast_1h = timeseries[1].get('data', {}).get('instant', {}).get('details', {}).get('air_temperature')
                    return curr, forecast_1h
        except: pass
        return None, None

    def fetch_polymarket_asks(self):
        results = {}
        try:
            r = requests.get(self.poly_url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data:
                    markets = data[0].get('markets', [])
                    for m in markets:
                        title = m.get('groupItemTitle', m.get('question'))
                        results[title] = m.get('bestAsk')
        except: pass
        return results

    def log_to_csv(self, timestamp, wd, prices):
        row = {
            "timestamp": timestamp,
            "consensus_actual": wd['avg_curr'],
            "consensus_forecast": wd['avg_fore'],
            "divergence": wd['divergence'],
            "NO_ACTUAL": wd['sources']['NOAA (METAR)']['curr'],
            "OM_ACTUAL": wd['sources']['Open-Meteo']['curr'],
            "OM_FORECAST": wd['sources']['Open-Meteo']['fore'],
            "MN_ACTUAL": wd['sources']['Met.no']['curr'],
            "MN_FORECAST": wd['sources']['Met.no']['fore']
        }
        row.update(prices)

        if not self.columns:
            base_cols = [
                "timestamp", "consensus_actual", "consensus_forecast", "divergence",
                "NO_ACTUAL", "OM_ACTUAL", "OM_FORECAST", "MN_ACTUAL", "MN_FORECAST"
            ]
            self.columns = base_cols + sorted(list(prices.keys()))

        exists = os.path.isfile(self.csv_file)
        with open(self.csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.columns)
            if not exists: writer.writeheader()
            writer.writerow(row)

    def display_dashboard(self, timestamp, weather_data, prices):
        is_tty = sys.stdout.isatty() and not self.no_tty
        CLEAR, BOLD, CYAN, GREEN, YELLOW, BLUE, RED, RESET = ("", "", "", "", "", "", "", "")
        if is_tty:
            CLEAR, BOLD, CYAN, GREEN, YELLOW, BLUE, RED, RESET = "\033[H\033[J", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[34m", "\033[31m", "\033[0m"
        
        if is_tty: print(CLEAR, end="")
        else: print("\n" + "="*30 + " [ New Log Entry ] " + "="*30)
        
        print(f"{BOLD}{CYAN}=" * 70)
        print(f"{BOLD}{CYAN} POLYMARKET WEATHER MONITOR - ADVANCED")
        print(f"{BOLD}{CYAN}=" * 70 + RESET)
        print(f"{BOLD}Update Time:{RESET} {timestamp} | {BOLD}Station:{RESET} {self.icao_code}")
        print(f"{BOLD}Target Event:{RESET} {self.event_slug}")
        print("-" * 70)
        sources = weather_data['sources']
        print(f"{BOLD}{'Weather Source':20} | {'Current':>10} | {'+1H Forecast':>12}{RESET}")
        print("-" * 70)
        for name, d in sources.items():
            curr = f"{YELLOW}{d['curr']:>9.1f}°C{RESET}" if d['curr'] is not None else f"{RED}{'N/A':>10}{RESET}"
            fore = f"{BLUE}{d['fore']:>11.1f}°C{RESET}" if d['fore'] is not None else f"{BLUE}{'N/A':>12}{RESET}"
            print(f"{name:20} | {curr} | {fore}")
        print("-" * 70)
        avg = weather_data['avg_curr']
        fore_avg = weather_data['avg_fore']
        trend_icon = "📈" if (fore_avg and avg and fore_avg > avg) else "📉"
        trend_color = GREEN if (fore_avg and avg and fore_avg > avg) else RED
        avg_str = f"{BOLD}{YELLOW}{avg:.2f}°C{RESET}" if avg else "N/A"
        fore_str = f"{BOLD}{trend_color}{fore_avg:.2f}°C {trend_icon}{RESET}" if fore_avg else "N/A"
        print(f"{BOLD}CONSENSUS CURRENT: {avg_str}    |    +1H FORECAST: {fore_str}")
        if weather_data['divergence'] > 0.8:
            print(f"{BOLD}{RED}⚠️  HIGH DIVERGENCE ALERT: Sources differ by {weather_data['divergence']:.1f}°C!{RESET}")
        print("-" * 70)
        if prices:
            print(f"{BOLD}{'Target Range':40} | {'Ask1':>10} | {'Confidence'}{RESET}")
            for title in sorted(prices.keys()):
                ask = prices[title]
                if ask:
                    bar_len = int(float(ask) * 15)
                    print(f"{title:40} | {GREEN}${ask:<8}{RESET} | {'█'*bar_len}{'░'*(15-bar_len)}")
        print("-" * 70)
        print(f"{BLUE}CSV: {self.csv_file}{RESET}")
        print(f"{BOLD}{CYAN}=" * 70 + RESET)

    def get_weather_data(self):
        """获取所有已激活数据源的数据并计算共识"""
        # 定义所有可用的数据获取方法
        source_fetchers = {
            "NOAA (METAR)": lambda: (self.fetch_noaa(), None),
            "Open-Meteo": self.fetch_open_meteo,
            "Met.no": self.fetch_met_no
        }
        
        sources = {}
        for name, fetcher in source_fetchers.items():
            try:
                curr, fore = fetcher()
                sources[name] = {"curr": curr, "fore": fore}
            except:
                sources[name] = {"curr": None, "fore": None}
                
        valid_curr = [v['curr'] for v in sources.values() if v['curr'] is not None]
        avg_curr = sum(valid_curr) / len(valid_curr) if valid_curr else None
        
        valid_fore = [v['fore'] for v in sources.values() if v['fore'] is not None]
        avg_fore = sum(valid_fore) / len(valid_fore) if valid_fore else None
        
        div = max(valid_curr) - min(valid_curr) if len(valid_curr) > 1 else 0
        return {"sources": sources, "avg_curr": avg_curr, "avg_fore": avg_fore, "divergence": div}

    def run_once(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        wd = self.get_weather_data()
        prices = self.fetch_polymarket_asks()
        
        self.display_dashboard(now, wd, prices)
        if wd['avg_curr'] is not None or prices:
            self.log_to_csv(now, wd, prices)

    def start(self, interval=60):
        while True:
            try: self.run_once()
            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weather Polymarket Edge Monitor")
    parser.add_argument("--preset", choices=PRESETS.keys(), help="Use a built-in preset (seoul/london)")
    parser.add_argument("--icao", help="ICAO station code (e.g., RKSI)")
    parser.add_argument("--slug", help="Polymarket event slug")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds")
    parser.add_argument("--no-tty", action="store_true", help="Disable ANSI escape codes for dashboard")
    
    args = parser.parse_args()
    
    # Priority: Direct arguments > Preset > Default (Seoul)
    conf = PRESETS["seoul"]
    if args.preset:
        conf = PRESETS[args.preset]
    
    icao = args.icao or conf["icao"]
    slug = args.slug or conf["slug"]
    lat = args.lat if args.lat is not None else conf["lat"]
    lon = args.lon if args.lon is not None else conf["lon"]
    
    monitor = WeatherPriceMonitor(icao, slug, lat, lon, no_tty=args.no_tty)
    try: monitor.start(args.interval)
    except KeyboardInterrupt: print("\nStopped.")
