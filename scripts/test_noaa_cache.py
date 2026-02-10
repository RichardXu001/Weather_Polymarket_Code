import requests
import time
import re
import os
from dotenv import load_dotenv
from datetime import datetime

# 加载环境变量
load_dotenv()

# 目标：安卡拉 LTAC
ICAO = "LTAC"
API_URL = f"https://www.aviationweather.gov/api/data/metar?ids={ICAO}&format=json"
CHECKWX_URL = f"https://api.checkwx.com/metar/{ICAO}/decoded"
# 注意：.env 中是 CheckWX_API_KEY
CHECKWX_KEY = os.getenv("CheckWX_API_KEY")

def fetch_api():
    try:
        start = time.time()
        r = requests.get(API_URL, timeout=10)
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            if data:
                raw = data[0].get('rawOb', '')
                obs_time = data[0].get('reportTime', 'Unknown')
                # 提取温度
                match = re.search(r'\s(M?\d{2})/(M?\d{2})\s', raw)
                temp = "N/A"
                if match:
                    t = match.group(1)
                    temp = -float(t[1:]) if t.startswith('M') else float(t)
                return temp, obs_time, elapsed
    except Exception as e:
        return f"Error: {e}", "", 0
    return None, "", 0

def fetch_checkwx():
    if not CHECKWX_KEY:
        return "No Key", "", 0
    try:
        start = time.time()
        headers = {"X-API-Key": CHECKWX_KEY}
        r = requests.get(CHECKWX_URL, headers=headers, timeout=10)
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            if data and data.get('data'):
                d = data['data'][0]
                obs_time = d.get('observed')
                temp = d.get('temperature', {}).get('celsius')
                return temp, obs_time, elapsed
    except Exception as e:
        return f"Error: {e}", "", 0
    return None, "", 0

if __name__ == "__main__":
    print(f"[{datetime.now()}] Comparing NOAA API vs CheckWX for {ICAO}...")
    print(f"Using Key: {CHECKWX_KEY[:4]}...{CHECKWX_KEY[-4:] if CHECKWX_KEY else ''}")
    print("-" * 75)
    
    api_t, api_obs, api_lat = fetch_api()
    cw_t, cw_obs, cw_lat = fetch_checkwx()
    
    print(f"{'SOURCE':10} | {'TEMP':5} | {'OBS_TIME (UTC)':25} | {'FETCH LATENCY'}")
    print("-" * 75)
    print(f"{'NOAA API':10} | {str(api_t):5} | {str(api_obs):25} | {api_lat:.3f}s")
    print(f"{'CheckWX':10} | {str(cw_t):5} | {str(cw_obs):25} | {cw_lat:.3f}s")
    print("-" * 75)
    
    if str(api_obs) == str(cw_obs):
        print("RESULT: Both sources are at the same observation time.")
    else:
        print(f"RESULT: DIVERGENCE! {'CheckWX' if str(cw_obs) > str(api_obs) else 'NOAA API'} is NEWER.")
