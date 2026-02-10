import requests
import time

ICAO = "LTAC"
API_URL = f"https://www.aviationweather.gov/api/data/metar?ids={ICAO}&format=json"
CACHE_URL = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"

def check_headers(url, name):
    print(f"\n--- Checking headers for {name} ---")
    start = time.time()
    r = requests.get(url, timeout=10)
    elapsed = time.time() - start
    print(f"URL: {url}")
    print(f"Status: {r.status_code}")
    print(f"Latency: {elapsed:.3f}s")
    for h in ["Last-Modified", "Date", "Cache-Control", "Age", "X-Cache", "ETag"]:
        print(f"{h}: {r.headers.get(h)}")
    return r

if __name__ == "__main__":
    check_headers(API_URL, "API")
    check_headers(CACHE_URL, "CACHE")
