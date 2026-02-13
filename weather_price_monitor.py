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
    def __init__(self, icao_code, event_slug, lat, lon, tz_offset=0, no_tty=False, city_name=None):
        self.icao_code = icao_code
        self.event_slug = event_slug
        self.lat = lat
        self.lon = lon
        self.tz_offset = tz_offset
        self.no_tty = no_tty
        self.city_name = city_name or icao_code.lower()
        
        # 频率控制与缓存 (V4.1 引入)
        self.last_om_data = (None, None)
        self.last_mn_data = (None, None)
        self.last_om_fetch_time = 0
        self.last_mn_fetch_time = 0

        # 网络鲁棒性：短重试 + 线性退避（仅处理瞬时网络抖动）
        self.transient_retries = max(0, int(os.getenv("HTTP_TRANSIENT_RETRIES", 1)))
        self.retry_backoff_seconds = max(0.0, float(os.getenv("HTTP_RETRY_BACKOFF_SECONDS", 0.8)))
        self.timeout_weather_seconds = float(os.getenv("HTTP_TIMEOUT_WEATHER_SECONDS", 10))
        self.timeout_poly_seconds = float(os.getenv("HTTP_TIMEOUT_POLY_SECONDS", 15))
        
        # 创建持久化会话与 User-Agent
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': f'WeatherMonitorBot/2.0 ({self.city_name}; rate-limiting-aware; contact: dev@example.com)'
        })

        # API URLs
        self.metar_url = f"https://www.aviationweather.gov/api/data/metar?ids={icao_code}&format=json"
        self.open_meteo_url = f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}&current_weather=true&hourly=temperature_2m"
        self.met_no_url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={self.lat}&lon={self.lon}"
        self.poly_url = f"https://gamma-api.polymarket.com/events?slug={event_slug}"
        
        # CSV Setup
        data_dir = "data/recordings"
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            
        start_time_str = datetime.now().strftime('%Y%m%d_%H%M')
        self.csv_file = f"{data_dir}/weather_recording_{self.city_name}_{start_time_str}.csv"
        self.columns = []

    def _get_with_retry(self, url, timeout, source_name):
        """对瞬时网络错误做短重试；429 交给上层逻辑处理。"""
        attempts = self.transient_retries + 1
        transient_status = {500, 502, 503, 504, 520, 521, 522, 523, 524}

        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.get(url, timeout=timeout)
                if resp.status_code in transient_status and attempt < attempts:
                    delay = self.retry_backoff_seconds * attempt
                    print(
                        f"⚠️ [{source_name}] Transient HTTP {resp.status_code} for {self.city_name}, "
                        f"retrying in {delay:.1f}s ({attempt}/{attempts - 1})"
                    )
                    time.sleep(delay)
                    continue
                return resp
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
            ) as e:
                if attempt < attempts:
                    delay = self.retry_backoff_seconds * attempt
                    print(
                        f"⚠️ [{source_name}] Transient {e.__class__.__name__} for {self.city_name}, "
                        f"retrying in {delay:.1f}s ({attempt}/{attempts - 1})"
                    )
                    time.sleep(delay)
                    continue
                raise

    def fetch_noaa(self):
        """Source 1: NOAA/METAR (Real-time Observation ONLY)"""
        try:
            r = self._get_with_retry(self.metar_url, timeout=self.timeout_weather_seconds, source_name="NOAA")
            if r.status_code == 200:
                data = r.json()
                if data:
                    raw = data[0].get('rawOb', '')
                    match = re.search(r'\s(M?\d{2})/(M?\d{2})\s', raw)
                    if match:
                        t = match.group(1)
                        return -float(t[1:]) if t.startswith('M') else float(t)
            elif r.status_code == 429:
                print(f"⚠️ [NOAA] Rate limit triggered (429) for {self.city_name}")
        except Exception as e:
            print(f"❌ [NOAA] Error fetching data for {self.city_name}: {e}")
        return None

    def fetch_open_meteo(self, interval=60):
        """Source 2: Open-Meteo (Current + Forecast) - 带频率控制与前向填充"""
        now = time.time()
        # 频率自律：未到采样时间，且已有缓存，则沿用缓存 (Forward Fill)
        if now - self.last_om_fetch_time < interval and self.last_om_data[0] is not None:
            return self.last_om_data

        try:
            r = self._get_with_retry(
                self.open_meteo_url,
                timeout=self.timeout_weather_seconds,
                source_name="Open-Meteo",
            )
            if r.status_code == 200:
                data = r.json()
                curr = data.get('current_weather', {}).get('temperature')
                hourly = data.get('hourly', {}).get('temperature_2m', [])
                forecast_1h = hourly[1] if len(hourly) > 1 else None
                self.last_om_data = (curr, forecast_1h)
                self.last_om_fetch_time = now
                return self.last_om_data
            elif r.status_code == 429:
                print(f"⚠️ [Open-Meteo] Rate limit triggered (429) for {self.city_name}, cooling down...")
                # 记录最后尝试时间，维持冷却
                self.last_om_fetch_time = now
            else:
                print(f"❌ [Open-Meteo] Unexpected Status {r.status_code} for {self.city_name}")
        except requests.exceptions.Timeout:
            print(f"⏳ [Open-Meteo] Request timeout for {self.city_name}")
        except Exception as e:
            print(f"❌ [Open-Meteo] Error: {e}")
            
        return self.last_om_data # 失败或流控时返回缓存

    def fetch_met_no(self, interval=60):
        """Source 3: Met.no (Current + Forecast) - 带频率控制与前向填充"""
        now = time.time()
        if now - self.last_mn_fetch_time < interval and self.last_mn_data[0] is not None:
            return self.last_mn_data

        try:
            r = self._get_with_retry(self.met_no_url, timeout=self.timeout_weather_seconds, source_name="Met.no")
            if r.status_code == 200:
                data = r.json()
                timeseries = data.get('properties', {}).get('timeseries', [])
                if timeseries:
                    curr = timeseries[0].get('data', {}).get('instant', {}).get('details', {}).get('air_temperature')
                    forecast_1h = timeseries[1].get('data', {}).get('instant', {}).get('details', {}).get('air_temperature')
                    self.last_mn_data = (curr, forecast_1h)
                    self.last_mn_fetch_time = now
                    return self.last_mn_data
            elif r.status_code == 429:
                print(f"⚠️ [Met.no] Rate limit triggered (429) for {self.city_name}, cooling down...")
                self.last_mn_fetch_time = now
            elif r.status_code >= 400:
                print(f"❌ [Met.no] Status {r.status_code} for {self.city_name}. Data might be missing.")
        except Exception as e:
            print(f"❌ [Met.no] Error: {e}")
            
        return self.last_mn_data

    def fetch_polymarket_asks(self):
        results = {}
        try:
            r = self._get_with_retry(self.poly_url, timeout=self.timeout_poly_seconds, source_name="Polymarket")
            if r.status_code == 200:
                data = r.json()
                if data:
                    markets = data[0].get('markets', [])
                    for m in markets:
                        # 统一使用 groupItemTitle 作为键，例如 "2°C"
                        title = m.get('groupItemTitle', m.get('question'))
                        yes_ask = m.get('bestAsk')
                        yes_bid = m.get('bestBid')
                        
                        # No 的报价推导：No Ask = 1 - Yes Bid, No Bid = 1 - Yes Ask
                        no_ask = (1 - float(yes_bid)) if yes_bid else None
                        no_bid = (1 - float(yes_ask)) if yes_ask else None
                        
                        results[title] = {
                            'yes_ask': yes_ask,
                            'yes_bid': yes_bid,
                            'no_ask': no_ask,
                            'no_bid': no_bid,
                            'vol': m.get('volumeClob')
                        }
            elif r.status_code == 429:
                print(f"⚠️ [Polymarket] Rate limit triggered (429) for {self.city_name}")
        except Exception as e:
            print(f"❌ Error fetching Polymarket prices for {self.city_name}: {e}")
        return results

    def fetch_all_sources(self, om_interval=60, mn_interval=60):
        """获取所有数据源 (取代原 get_weather_data 以兼容 bot 调用)"""
        source_fetchers = {
            "NOAA (METAR)": lambda: (self.fetch_noaa(), None),
            "Open-Meteo": lambda: self.fetch_open_meteo(om_interval),
            "Met.no": lambda: self.fetch_met_no(mn_interval)
        }
        
        sources = {}
        for name, fetcher in source_fetchers.items():
            try:
                curr, fore = fetcher()
                sources[name] = {"curr": curr, "fore": fore}
            except Exception as e:
                print(f"⚠️ Unexpected fetcher error for {name}: {e}")
                sources[name] = {"curr": None, "fore": None}
                
        valid_curr = [v['curr'] for v in sources.values() if v['curr'] is not None]
        avg_curr = sum(valid_curr) / len(valid_curr) if valid_curr else None
        
        valid_fore = [v['fore'] for v in sources.values() if v['fore'] is not None]
        avg_fore = sum(valid_fore) / len(valid_fore) if valid_fore else None
        
        div = (max(valid_curr) - min(valid_curr)) if len(valid_curr) > 1 else 0
        return {"sources": sources, "avg_curr": avg_curr, "avg_fore": avg_fore, "divergence": div}

    def get_weather_data(self):
        """保持向前兼容"""
        return self.fetch_all_sources()

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
        
        # 录制 Yes/No 的 Ask1/Bid1 与成交量
        for title, p_data in prices.items():
            if isinstance(p_data, dict):
                row[f"{title}_yes_ask"] = p_data.get('yes_ask')
                row[f"{title}_yes_bid"] = p_data.get('yes_bid')
                row[f"{title}_no_ask"] = p_data.get('no_ask')
                row[f"{title}_no_bid"] = p_data.get('no_bid')
                row[f"{title}_vol"] = p_data.get('vol')

        if not self.columns:
            # 基础列
            base_cols = [
                "timestamp", "consensus_actual", "consensus_forecast", "divergence",
                "NO_ACTUAL", "OM_ACTUAL", "OM_FORECAST", "MN_ACTUAL", "MN_FORECAST"
            ]
            # 动态推导价格列 (即便当前没有拿到，理想情况下以后会有)
            price_cols = []
            for title in sorted(prices.keys()):
                price_cols.extend([
                    f"{title}_yes_ask", f"{title}_yes_bid", 
                    f"{title}_no_ask", f"{title}_no_bid", 
                    f"{title}_vol"
                ])
            self.columns = base_cols + price_cols

        exists = os.path.isfile(self.csv_file)
        with open(self.csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.columns, extrasaction='ignore')
            if not exists: writer.writeheader()
            writer.writerow(row)

    def display_dashboard(self, timestamp, local_time_str, weather_data, prices):
        is_tty = sys.stdout.isatty() and not self.no_tty
        CLEAR, BOLD, CYAN, GREEN, YELLOW, BLUE, RED, RESET = ("", "", "", "", "", "", "", "")
        if is_tty:
            CLEAR, BOLD, CYAN, GREEN, YELLOW, BLUE, RED, RESET = "\033[H\033[J", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[34m", "\033[31m", "\033[0m"
        
        if is_tty: print(CLEAR, end="")
        else: print("\n" + "="*30 + " [ New Log Entry ] " + "="*30)
        
        print(f"{BOLD}{CYAN}=" * 80)
        print(f"{BOLD}{CYAN} POLYMARKET WEATHER MONITOR - ADVANCED (BID/ASK/LAST)")
        print(f"{BOLD}{CYAN}=" * 80 + RESET)
        print(f"{BOLD}Update Time:{RESET} {timestamp} | {BOLD}Local Time:{RESET} {local_time_str} ({self.tz_offset:+d}h)")
        print(f"{BOLD}Station:{RESET} {self.icao_code} | {BOLD}Target Event:{RESET} {self.event_slug}")
        print("-" * 80)
        sources = weather_data['sources']
        print(f"{BOLD}{'Weather Source':20} | {'Current':>10} | {'+1H Forecast':>12}{RESET}")
        print("-" * 80)
        for name, d in sources.items():
            curr = f"{YELLOW}{d['curr']:>9.1f}°C{RESET}" if d['curr'] is not None else f"{RED}{'N/A':>10}{RESET}"
            fore = f"{BLUE}{d['fore']:>11.1f}°C{RESET}" if d['fore'] is not None else f"{BLUE}{'N/A':>12}{RESET}"
            print(f"{name:20} | {curr} | {fore}")
        print("-" * 80)
        avg = weather_data['avg_curr']
        fore_avg = weather_data['avg_fore']
        trend_icon = "📈" if (fore_avg and avg and fore_avg > avg) else "📉"
        trend_color = GREEN if (fore_avg and avg and fore_avg > avg) else RED
        avg_str = f"{BOLD}{YELLOW}{avg:.2f}°C{RESET}" if avg else "N/A"
        fore_str = f"{BOLD}{trend_color}{fore_avg:.2f}°C {trend_icon}{RESET}" if fore_avg else "N/A"
        print(f"{BOLD}CONSENSUS CURRENT: {avg_str}    |    +1H FORECAST: {fore_str}")
        if weather_data['divergence'] > 0.8:
            print(f"{BOLD}{RED}⚠️  HIGH DIVERGENCE ALERT: Sources differ by {weather_data['divergence']:.1f}°C!{RESET}")
        print("-" * 80)
        if prices:
            print(f"{BOLD}{'Target Range':35} | {'YES Bid/Ask':>13} | {'NO Bid/Ask':>13} | {'Confidence'}{RESET}")
            for title in sorted(prices.keys()):
                p = prices[title]
                # Confidence bar based on YES ASK price
                ref_price = float(p.get('yes_ask') or 0)
                bar_len = int(ref_price * 15)
                
                yes_ask = p.get('yes_ask')
                yes_bid = p.get('yes_bid')
                no_ask = p.get('no_ask')
                no_bid = p.get('no_bid')
                
                y_ask_str = f"{GREEN}${yes_ask:<5}{RESET}" if yes_ask else "N/A"
                y_bid_str = f"{RED}${yes_bid:<5}{RESET}" if yes_bid else "N/A"
                n_ask_str = f"{YELLOW}${no_ask:<5}{RESET}" if no_ask else "N/A"
                n_bid_str = f"{BLUE}${no_bid:<5}{RESET}" if no_bid else "N/A"
                
                print(f"{title:35} | {y_bid_str}/{y_ask_str} | {n_bid_str}/{n_ask_str} | {'█'*bar_len}{'░'*(15-bar_len)}")
        print("-" * 80)
        print(f"{BLUE}CSV: {self.csv_file}{RESET}")
        print(f"{BOLD}{CYAN}=" * 80 + RESET)


    def run_once(self):
        from datetime import timezone
        
        # System time (usually UTC on servers)
        now_utc = datetime.now(timezone.utc)
        now_str = now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
        
        # Calculate Local Time
        local_dt = now_utc + timedelta(hours=self.tz_offset)
        local_time_str = local_dt.strftime('%H:%M:%S')
        
        wd = self.get_weather_data()
        prices = self.fetch_polymarket_asks()
        
        self.display_dashboard(now_str, local_time_str, wd, prices)
        if wd['avg_curr'] is not None or prices:
            self.log_to_csv(now_str, wd, prices)

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
    parser.add_argument("--interval", type=int, default=10, help="Refresh interval in seconds")
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
    tz_offset = conf.get("tz_offset", 0)
    
    city_name = args.preset if args.preset else (args.icao.lower() if args.icao else "manual")
    monitor = WeatherPriceMonitor(icao, slug, lat, lon, tz_offset, no_tty=args.no_tty, city_name=city_name)
    try: monitor.start(args.interval)
    except KeyboardInterrupt: print("\nStopped.")
