import pandas as pd
import os
from engine.strategy import StrategyKernel
from engine.data_feed import WeatherState
from engine.config import QuantConfig
from datetime import datetime

def test_recording_backtest(csv_path):
    print(f"[*] 正在验证录制文件: {csv_path}")
    if not os.path.exists(csv_path):
        print("[!] 文件不存在")
        return

    df = pd.read_csv(csv_path)
    print(f"[*] 总采样点: {len(df)}")
    print(f"[*] 列名: {df.columns.tolist()}")

    # 尝试映射到回测逻辑
    state = WeatherState(timestamp="", local_hour=0.0)
    cfg = QuantConfig
    
    # 模拟前 5 条数据的步进
    success_count = 0
    for idx, row in df.head(5).iterrows():
        try:
            # 兼容性映射
            state.om_now = row['om_actual']
            state.mn_now = row['mn_actual']
            state.noaa_now = row['noaa_actual']
            state.actual_now = row['actual_now']
            state.local_hour = float(row['local_hour'])
            
            # 此时可以运行策略内核
            state.target_temp = 8.0 
            signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
            print(f" - Row {idx}: TS={row['timestamp']} | V_fit={meta['v_fit']:.2f} | Signal={signal}")
            success_count += 1
        except Exception as e:
            print(f" [!] Row {idx} 映射失败: {e}")

    if success_count == 5:
        print("[SUCCESS] 录制数据结构足以驱动策略内核！")
    else:
        print("[FAILURE] 数据映射存在问题。")

if __name__ == "__main__":
    test_recording_backtest("./data/recordings/weather_recording_seoul.csv")
