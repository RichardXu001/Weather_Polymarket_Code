import pandas as pd
from engine.strategy import StrategyKernel
from engine.data_feed import WeatherState
from engine.config import QuantConfig
from datetime import datetime
import os

def run_detail_check(csv_path="data/server_data/weather_edge_EGLC_20260205_1514.csv"):
    df = pd.read_csv(csv_path)
    cfg = QuantConfig
    cfg.STATION_TZ_OFFSET = 0 # London
    
    state = WeatherState(timestamp="", local_hour=0.0)
    state.target_temp = 8.0 # 我们关注 8 度关口
    
    trades = []
    
    for idx, row in df.iterrows():
        ts_str = row['timestamp']
        ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        london_hour = (ts.hour - 8) % 24 + ts.minute / 60.0
        
        state.timestamp = ts_str
        state.local_hour = london_hour
        state.om_now = row.get('OM_ACTUAL')
        state.mn_now = row.get('MN_ACTUAL')
        state.forecast_1h = row.get('consensus_forecast')
        state.actual_now = row.get('consensus_actual')
        
        if state.om_now is not None: state.om_history.append(state.om_now)
        if state.mn_now is not None: state.mn_history.append(state.mn_now)
        
        v_fit = (state.om_now * cfg.W1_OM + state.mn_now * cfg.W2_MN) if (state.om_now and state.mn_now) else None
        if v_fit:
            state.v_fit_history.append(v_fit)
        
        for h in [state.om_history, state.mn_history, state.v_fit_history]:
            if len(h) > 10: h.pop(0)
            
        signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
        
        if signal == 'BUY':
            # 找到 8°C 的价格
            price_8 = row.get('8°C', 'N/A')
            trades.append({
                "ts": ts_str,
                "london": f"{london_hour:.2f}",
                "v_fit": f"{v_fit:.2f}",
                "price": price_8,
                "contract": "8°C"
            })
            
    print("-" * 80)
    print(f"{'北京时间':20} | {'伦敦时间':10} | {'V_fit':8} | {'买入合约':10} | {'价格(Best Ask)'}")
    print("-" * 80)
    
    if not trades:
        print("未发现信号。")
    else:
        # 显示全部 3 个信号（之前由于过滤只展示了2个）
        for t in trades:
            print(f"{t['ts']:20} | {t['london']:<10} | {t['v_fit']:<8} | {t['contract']:<10} | {t['price']}")
    print("-" * 80)

if __name__ == "__main__":
    run_detail_check()
