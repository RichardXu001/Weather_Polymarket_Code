import requests
import json
import time
from datetime import datetime

class PolyMonitor:
    def __init__(self, event_slug="highest-temperature-in-seoul-on-january-30"):
        self.event_slug = event_slug
        self.api_url = f"https://gamma-api.polymarket.com/events?slug={event_slug}"

    def fetch_market_data(self):
        try:
            r = requests.get(self.api_url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data and len(data) > 0:
                    return data[0].get('markets', [])
        except Exception as e:
            print(f"Fetch Error: {e}")
        return None

    def get_yes_price(self, market):
        try:
            # Gamma API 返回的 outcomePrices 往往是一个 JSON 格式的字符串，如 '["0.1", "0.9"]'
            raw_prices = market.get('outcomePrices')
            if isinstance(raw_prices, str):
                prices = json.loads(raw_prices)
            else:
                prices = raw_prices
                
            if prices and len(prices) > 0:
                return float(prices[0])
        except Exception as e:
            # print(f"Parse error: {e}")
            pass
        return None

    def monitor(self, interval=30):
        print(f"[*] Starting Polymarket monitor for slug: {self.event_slug}...")
        while True:
            markets = self.fetch_market_data()
            if markets:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Market Update:")
                for m in markets:
                    question = m.get('question')
                    outcomes = m.get('outcomes')
                    prices = m.get('outcomePrices')
                    print(f"  Q: {question}")
                    if outcomes and prices:
                        for o, p in zip(outcomes, prices):
                            print(f"    {o}: ${p}")
                print("-" * 40)
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Failed to fetch data.")
            time.sleep(interval)

if __name__ == "__main__":
    monitor = PolyMonitor()
    # 执行一次性获取显示
    print(f"[*] Fetching initial data for: {monitor.event_slug}")
    data = monitor.fetch_market_data()
    if data:
        for m in data:
            print(f"\nMarket: {m.get('question')}")
            outcomes = m.get('outcomes')
            prices = m.get('outcomePrices')
            
            # 严格处理 prices 可能为 JSON 字符串的情况
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except:
                    pass
            
            # Outcomes 有时也是字符串形式的 JSON 数组
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except:
                    pass

            if outcomes and prices:
                for o, p in zip(outcomes, prices):
                    print(f"  {o}: {p}")
    else:
        print("Failed to fetch Polymarket data.")
