import requests
from datetime import datetime, timezone, timedelta

def search_active_markets():
    # 当前 UTC 时间
    utc_now = datetime.now(timezone.utc)
    print(f"[*] 当前 UTC 时间: {utc_now.strftime('%H:%M')}")
    
    # 目标：寻找当地时间 14:00 - 16:00 的城市
    # local_time = utc_now + offset
    # 14 <= (utc_hour + offset) <= 16
    # offset 范围: [14 - utc_hour, 16 - utc_hour]
    
    utc_hour = utc_now.hour + utc_now.minute / 60.0
    min_offset = 14.0 - utc_hour
    max_offset = 16.0 - utc_hour
    
    print(f"[*] 目标时区偏移范围: UTC{min_offset:+.1f} 到 UTC{max_offset:+.1f}")

    # Polymarket Gamma API (搜索 "Temperature")
    url = "https://gamma-api.polymarket.com/events?limit=50&active=true&closed=false&q=Temperature"
    try:
        resp = requests.get(url)
        data = resp.json()
    except Exception as e:
        print(f"[!] API 请求失败: {e}")
        return

    print(f"[*] 搜索到 {len(data)} 个相关事件，正在筛选...")
    
    candidates = []
    
    for event in data:
        title = event.get('title', '')
        slug = event.get('slug', '')
        
        # 简单时区映射 (常见大城市)
        city_offset = None
        
        # 欧洲/非洲 (UTC+0 ~ UTC+2)
        if 'London' in title: city_offset = 0
        elif 'Paris' in title: city_offset = 1
        elif 'Berlin' in title: city_offset = 1
        elif 'Madrid' in title: city_offset = 1
        elif 'Rome' in title: city_offset = 1
        elif 'Cairo' in title: city_offset = 2
        elif 'Lagos' in title: city_offset = 1
        elif 'Dubai' in title: city_offset = 4
        elif 'Moscow' in title: city_offset = 3

        # 美洲 (UTC-5 ~ UTC-8) - 下午肯定不是现在
        elif 'New York' in title: city_offset = -5
        elif 'Los Angeles' in title: city_offset = -8
        elif 'Chicago' in title: city_offset = -6
        
        # 亚洲 (UTC+8 ~ UTC+9) - 已经是晚上了
        elif 'Seoul' in title: city_offset = 9
        elif 'Tokyo' in title: city_offset = 9
        elif 'Beijing' in title: city_offset = 8
        elif 'Singapore' in title: city_offset = 8
        elif 'Hong Kong' in title: city_offset = 8
        
        if city_offset is not None:
            local_time = utc_now + timedelta(hours=city_offset)
            local_hour = local_time.hour + local_time.minute / 60.0
            
            # 放宽到 12:00 - 16:00 (包含欧洲午间)
            if 12.0 <= local_hour <= 16.0:
                print(f"\n[VIABLE CANDIDATE] Found!")
                print(f"  Title: {title}")
                print(f"  Slug: {slug}")
                print(f"  Time: {local_time.strftime('%H:%M')} (UTC{city_offset:+d})")
                candidates.append(event)
            else:
                pass # print(f"  [Skip] {title} - Local time: {local_time.strftime('%H:%M')}")
        else:
             pass # print(f"  [Unmapped] {title}")

    if not candidates:
        print("\n[-] 未找到处于当地时间 14:00-16:00 的已知城市合约。")
        print("    目前适合的时区只有 UTC+3 左右 (如莫斯科、伊斯坦布尔、东非)。")

if __name__ == "__main__":
    search_active_markets()
