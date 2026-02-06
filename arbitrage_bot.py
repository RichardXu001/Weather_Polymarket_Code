import time
import os
from datetime import datetime
from metar_monitor import MetarMonitor
from poly_monitor import PolyMonitor

class WeatherArbitrageBot:
    def __init__(self, icao_code="RKSI", event_slug="highest-temperature-in-seoul-on-january-25"):
        self.metar = MetarMonitor(icao_code)
        self.poly = PolyMonitor(event_slug)

    def clear_screen(self):
        # 清屏以便展示 Dashboard 效果
        os.system('clear' if os.name == 'posix' else 'cls')

    def render_dashboard(self, obs_time, metar_temp, markets):
        self.clear_screen()
        print("=" * 70)
        print(f"  POLYMARKET WEATHER ARBITRAGE DASHBOARD  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print("=" * 70)
        print(f"  [METAR SOURCE] Station: RKSI (Incheon Intl)")
        print(f"  [OBSERVATION ] Time: {obs_time} (UTC)")
        print(f"  [REAL TEMPERATURE] ---->  {metar_temp}°C  <----")
        print("-" * 70)
        print(f"{'Market Question':<50} | {'Price':<8} | {'Status'}")
        print("-" * 70)

        for m in markets:
            question = m.get('question', '')
            # 简化问题显示
            short_q = question.replace("Will the highest temperature in Seoul be ", "").replace(" on January 25?", "")
            
            yes_price = self.poly.get_yes_price(m)
            price_str = f"${yes_price:.4f}" if yes_price is not None else "N/A"
            
            # 判断套利状态
            status = " "
            try:
                import re
                # 处理负数温度的正则
                nums = re.findall(r'-?\d+', short_q)
                if nums:
                    target = int(nums[0])
                    if metar_temp >= target and yes_price is not None and yes_price < 0.90:
                        status = "🔥 BUY YES!"
                    elif metar_temp < target and yes_price is not None and yes_price > 0.10:
                        # 这种情况通常是价格还没反应过来温度已经降了（对于“低于”市场）
                        # 但此处逻辑主要针对“达到”市场
                        pass
            except:
                pass

            print(f"{short_q[:48]:<50} | {price_str:<8} | {status}")
        
        print("=" * 70)
        print("  Tips: Prices are polled from Gamma API | METAR from NOAA")

    def run(self, interval=30):
        while True:
            try:
                obs_time, raw = self.metar.fetch_latest_metar()
                m_temp = self.metar.parse_temperature(raw) if raw else None
                markets = self.poly.fetch_market_data()
                
                if m_temp is not None and markets:
                    self.render_dashboard(obs_time, m_temp, markets)
                else:
                    print("Waiting for data...")
            except Exception as e:
                print(f"Dashboard error: {e}")
            
            time.sleep(interval)

if __name__ == "__main__":
    bot = WeatherArbitrageBot()
    bot.run()
