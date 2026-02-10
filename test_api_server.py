import requests
import json
from datetime import datetime, timedelta

def get_slug(template, offset):
    now = datetime.utcnow() + timedelta(hours=offset)
    month = now.strftime("%B").lower()
    return template.format(month=month, day=now.day, year=now.year)

def test_market(name, template, offset):
    slug = get_slug(template, offset)
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    print(f"Checking {name}: {slug}")
    try:
        r = requests.get(url, timeout=10)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if data:
                markets = data[0].get('markets', [])
                print(f"Found {len(markets)} markets")
                for m in markets:
                    title = m.get('groupItemTitle')
                    ask = m.get('bestAsk')
                    bid = m.get('bestBid')
                    vol = m.get('volumeClob')
                    print(f"  - {title}: Ask={ask}, Bid={bid}, Vol={vol}")
            else:
                print("  - No data found for this slug")
    except Exception as e:
        print(f"  - Error: {e}")

if __name__ == "__main__":
    test_market("Seoul", "highest-temperature-in-seoul-on-{month}-{day}-{year}", 9)
    print("-" * 20)
    test_market("London", "highest-temperature-in-london-on-{month}-{day}-{year}", 0)
    print("-" * 20)
    test_market("Ankara", "highest-temperature-in-ankara-on-{month}-{day}-{year}", 3)
