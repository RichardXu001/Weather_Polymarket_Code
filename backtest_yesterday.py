import pandas as pd
from engine.strategy import StrategyKernel
from engine.data_feed import WeatherState
from engine.config import QuantConfig
from datetime import datetime
import json
import os

def load_presets(json_path="locations.json"):
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def run_backtest(preset_name="london", csv_path="data/server_data/weather_edge_EGLC_20260205_1514.csv"):
    print(f"[*] 启动地点感知回测 | 站点: {preset_name} | 数据源: {csv_path}")
    
    presets = load_presets()
    if preset_name not in presets:
        print(f"[!] 找不到预置: {preset_name}")
        return
    
    conf = presets[preset_name]
    tz_offset = conf.get("tz_offset", 0)
    cfg = QuantConfig
    
    # 强制同步时区到配置（策略内核会引用）
    cfg.STATION_TZ_OFFSET = tz_offset
    
    df = pd.read_csv(csv_path)
    state = WeatherState(timestamp="", local_hour=0.0)
    
    trades = []
    reasons_stat = {}
    
    for idx, row in df.iterrows():
        # 1. 换算本地时间 (Beijing UTC+8 -> Station UTC+Offset)
        ts_str = row['timestamp']
        ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        station_hour = (ts.hour + (tz_offset - 8)) % 24 + ts.minute / 60.0
        
        # 2. 更新状态
        state.timestamp = ts_str
        state.local_hour = station_hour
        state.om_now = row.get('OM_ACTUAL')
        state.mn_now = row.get('MN_ACTUAL')
        state.noaa_now = row.get('NO_ACTUAL')
        state.forecast_1h = row.get('consensus_forecast')
        state.actual_now = row.get('consensus_actual')
        
        # 模拟历史
        if state.om_now is not None: state.om_history.append(state.om_now)
        if state.mn_now is not None: state.mn_history.append(state.mn_now)
        
        v_fit_now = (state.om_now * cfg.W1_OM + state.mn_now * cfg.W2_MN) if (state.om_now and state.mn_now) else None
        if v_fit_now:
            state.v_fit_history.append(v_fit_now)
            
        for h in [state.om_history, state.mn_history, state.v_fit_history]:
            if len(h) > 10: h.pop(0)
            
        # 3. 决策
        state.target_temp = 8.0 # London 2/5 交易基准
        signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
        
        if signal == 'BUY':
            trades.append({
                "ts": ts_str,
                "local": f"{station_hour:.2f}",
                "v_fit": f"{meta['v_fit']:.2f}",
                "reason": reason
            })
        else:
            r_key = reason.split('(')[0].split(':')[0].strip()
            reasons_stat[r_key] = reasons_stat.get(r_key, 0) + 1

    print("\n" + "="*60)
    print(f"回测汇总 | {preset_name.upper()}")
    print(f"采样点: {len(df)} | 触发信号: {len(trades)}")
    print("-" * 60)
    for r, count in sorted(reasons_stat.items(), key=lambda x: x[1], reverse=True):
        print(f" - {r:45}: {count} 次")
    print("="*60)
    
    if trades:
        print(f"{'北京时间':20} | {'站点本地':10} | {'V_fit':8} | {'触发逻辑'}")
        print("-" * 75)
        last_t = -1
        for t in trades:
            curr_t = float(t['local'])
            if abs(curr_t - last_t) > 0.05:
                print(f"{t['ts']:20} | {t['local']:<10} | {t['v_fit']:<8} | {t['reason']}")
                last_t = curr_t

if __name__ == "__main__":
    run_backtest("london")
