import requests
import re
import time
from datetime import datetime, timedelta

class MetarMonitor:
    def __init__(self, icao_code="RKSI"):
        self.icao_code = icao_code
        self.last_raw = None
        # 使用 Aviation Weather API，通常比静态文本服务器更新更快
        self.url = f"https://www.aviationweather.gov/api/data/metar?ids={icao_code}&format=json"

    def fetch_latest_metar(self):
        """从 Aviation Weather API 获取最新的 METAR 数据"""
        try:
            response = requests.get(self.url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    latest = data[0]
                    metar_raw = latest.get('rawOb', '')
                    # 观测时间处理
                    obs_time_raw = latest.get('reportTime', '') # 例如 "2026-01-25 14:30:00"
                    
                    # 转换显示逻辑：如果是 UTC 14:30，加 9 小时就是首尔 23:30
                    try:
                        utc_dt = datetime.strptime(obs_time_raw, "%Y-%m-%d %H:%M:%S")
                        local_dt = utc_dt + timedelta(hours=9)
                        obs_display = f"{obs_time_raw} UTC (Seoul: {local_dt.strftime('%H:%M')})"
                    except:
                        obs_display = obs_time_raw
                        
                    return obs_display, metar_raw
        except Exception as e:
            print(f"Error fetching METAR: {e}")
        return None, None

    def parse_temperature(self, metar_raw):
        """
        从 METAR 报文中解析温度。格式通常为: 32/14 或 M01/M03 (表示负数)
        """
        # 正则匹配温度部分: 空格 + 两位数字(可选M开头) + / + 两位数字(可选M开头)
        match = re.search(r'\s(M?\d{2})/(M?\d{2})\s', metar_raw)
        if match:
            temp_str = match.group(1)
            if temp_str.startswith('M'):
                return -int(temp_str[1:])
            else:
                return int(temp_str)
        return None

    def monitor(self, interval=60):
        print(f"[*] Starting METAR monitor for {self.icao_code}...")
        while True:
            timestamp, raw = self.fetch_latest_metar()
            if raw and raw != self.last_raw:
                self.last_raw = raw
                temp = self.parse_temperature(raw)
                self.last_temp = temp
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] New METAR for {self.icao_code}:")
                print(f"  Raw: {raw}")
                print(f"  Temp: {temp}°C")
                print("-" * 40)
            
            time.sleep(interval)

if __name__ == "__main__":
    monitor = MetarMonitor("RKSI")
    # 单次测试
    ts, raw = monitor.fetch_latest_metar()
    if raw:
        print(f"Latest METAR (RKSI): {raw}")
        print(f"Parsed Temp: {monitor.parse_temperature(raw)}°C")
    else:
        print("Failed to fetch METAR.")
