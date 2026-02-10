import gzip
import requests
import io

CACHE_URL = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
STATIONS = ["LTAC", "RKSI", "EGLL"]

def debug_cache():
    r = requests.get(CACHE_URL, timeout=10)
    if r.status_code == 200:
        with gzip.open(io.BytesIO(r.content), 'rt') as f:
            lines = f.readlines()
            print(f"Total lines: {len(lines)}")
            # 打印列头
            for i in range(10):
                if "station_id" in lines[i] or i == 5:
                    print(f"Possible header (Line {i}): {lines[i].strip()}")
            
            for s in STATIONS:
                found = False
                for line in lines:
                    if s in line:
                        print(f"FOUND {s}: {line.strip()}")
                        found = True
                        break
                if not found:
                    print(f"NOT FOUND: {s}")

if __name__ == "__main__":
    debug_cache()
