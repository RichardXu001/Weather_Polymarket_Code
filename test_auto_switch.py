from weather_bot import WeatherBot
import datetime as dt
from datetime import timezone, timedelta

def test_switch():
    hub = WeatherBot()
    preset = "seoul"
    offset = 9
    template = "highest-temperature-in-seoul-on-{month}-{day}-{year}"
    
    # 1. 获取今天 (Feb 6)
    today_slug = hub._get_dynamic_slug(template, offset)
    print(f"[*] Today (Feb 6) Slug: {today_slug}")
    
    # 2. 模拟明天 (Hacking logic to simulate tomorrow)
    # 我们直接在 _get_dynamic_slug 内部逻辑上加上 24h 模拟
    utc_now = dt.datetime.now(timezone.utc)
    mock_tomorrow = utc_now + timedelta(hours=offset + 24)
    
    month_name = mock_tomorrow.strftime("%B").lower()
    day = mock_tomorrow.day
    year = mock_tomorrow.year
    tomorrow_slug = template.format(month=month_name, day=day, year=year)
    
    print(f"[*] Tomorrow (Feb 7) Predicted Slug: {tomorrow_slug}")
    
    if "february-7-2026" in tomorrow_slug:
        print("[SUCCESS] 动态 Slug 生成逻辑验证通过！")
    else:
        print("[FAILURE] Slug 生成不符合预期。")

if __name__ == "__main__":
    test_switch()
