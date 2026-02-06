import pandas as pd
import argparse
import json
import os
import re
from datetime import datetime
from engine.strategy import StrategyKernel
from engine.data_feed import WeatherState
from engine.config import QuantConfig

def load_presets(json_path="locations.json"):
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    print(f"[!] 警告: 找不到预置文件 {json_path}")
    return {}

def extract_target_from_col(col_name):
    """从列名提取数值阈值 (如 price_8°C -> 8.0)"""
    # 提取数字（包括负号和小数点）
    match = re.search(r'(-?\d+(\.\d+)?)', col_name)
    return float(match.group(1)) if match else None

def run_backtest(csv_path, preset_name=None):
    if not os.path.exists(csv_path):
        print(f"[错误] 数据文件不存在: {csv_path}")
        return

    # 1. 加载配置
    presets = load_presets()
    conf = presets.get(preset_name, {})
    tz_offset = conf.get("tz_offset", 0)
    cfg = QuantConfig
    cfg.STATION_TZ_OFFSET = tz_offset
    
    print(f"[*] 启动全市场扫描回测 | 时区偏移: {tz_offset}")
    print("-" * 80)

    # 2. 读取数据与识别合约
    df = pd.read_csv(csv_path)
    
    # 兼容性合约识别: 匹配 price_X°C 或直接匹配 X°C, X°C or below, X°C or higher
    price_cols = [c for c in df.columns if c.startswith('price_') or '°C' in c]
    target_map = {c: extract_target_from_col(c) for c in price_cols}
    active_targets = {c: t for c, t in target_map.items() if t is not None}
    
    print(f"[*] 识别到 {len(active_targets)} 档合约选项: {sorted(list(active_targets.values()))}")
    
    state = WeatherState(timestamp="", local_time="", local_hour=0.0)
    trades = []
    
    # 3. 循环仿真
    for idx, row in df.iterrows():
        ts_str = row['timestamp']
        # 状态更新
        state.timestamp = ts_str
        state.local_time = row.get('local_time', '--:--')
        
        # [Fix] 提前初始化 v_fit，避免后续作用域错误
        v_fit = None
        
        # 兼容性时间计算
        try:
            ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
            state.local_hour = (ts.hour + (tz_offset - 8)) % 24 + ts.minute / 60.0
            # 为旧格式 CSV 补全 local_time 字段
            if state.local_time == '--:--':
                state.local_time = f"{int(state.local_hour):02d}:{int((state.local_hour % 1) * 60):02d}"
        except:
            state.local_hour = float(row.get('local_hour', 0))

        # 映射原始天气数据 (兼容新旧字段名)
        state.noaa_curr = row.get('noaa_curr') if 'noaa_curr' in row else row.get('NO_ACTUAL')
        state.om_curr = row.get('om_curr') if 'om_curr' in row else row.get('OM_ACTUAL')
        state.om_fore = row.get('om_fore') if 'om_fore' in row else row.get('OM_FORECAST')
        state.mn_curr = row.get('mn_curr') if 'mn_curr' in row else row.get('MN_ACTUAL')
        state.mn_fore = row.get('mn_fore') if 'mn_fore' in row else row.get('MN_FORECAST')
        
        # 实时计算共识值
        valid_curr = [v for v in [state.noaa_curr, state.om_curr, state.mn_curr] if v is not None]
        state.consensus_curr = sum(valid_curr) / len(valid_curr) if valid_curr else None
        valid_fore = [v for v in [state.om_fore, state.mn_fore] if v is not None]
        state.consensus_fore = sum(valid_fore) / len(valid_fore) if valid_fore else None
        
        v_fit = None
        # 兼容旧代码
        state.noaa_now = state.noaa_curr
        state.om_now = state.om_curr
        state.mn_now = state.mn_curr
        state.actual_now, state.forecast_1h = state.consensus_curr, state.consensus_fore

        # [Fix] 在进入合约判定前，必须先算出当前的 v_fit，否则过滤条件无效
        # 公式必须与 StrategyKernel 保持 100% 一致: W1*OM + W2*MN + BIAS
        if state.om_now is not None and state.mn_now is not None:
             v_fit = state.om_now * cfg.W1_OM + state.mn_now * cfg.W2_MN + cfg.BIAS
             # 这里不需要 append 历史，因为 StrategyKernel 会负责 append。
             # 我们只是为了过滤用。

        # [重要] 维护天气源历史，否则 StrategyKernel 无法判定趋势
        if state.noaa_now is not None: state.noaa_history.append(state.noaa_now)
        if state.om_now is not None: state.om_history.append(state.om_now)
        if state.mn_now is not None: state.mn_history.append(state.mn_now)
        
        # 限制历史长度 (与实盘对齐)
        for hist in [state.noaa_history, state.om_history, state.mn_history]:
            if len(hist) > 10: hist.pop(0)

        # 全量档位决策判定
        tick_opportunity_found = False
        for col_name, target in active_targets.items():
            state.target_temp = target
            ask_price = row.get(col_name, 0)
            # 智能过滤: 只推荐与 predict_noaa(v_fit) 完全匹配的合约
            # 使用与实盘相同的 WeatherModel.predict_noaa() 确保逻辑一致
            from engine.models import WeatherModel
            import math
            if v_fit is not None and not math.isnan(v_fit):
                target_prediction = WeatherModel.predict_noaa(v_fit)
                if target != target_prediction:
                    continue
            elif v_fit is None or (v_fit is not None and math.isnan(v_fit)):
                # 数据不完整，跳过本行所有合约
                continue

            # 仅在有报价的情况下进行模拟
            if ask_price > 0:
                # 执行策略判定
                signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
                
                # [关键修正] 因为我们是在同一个 state 上循环跑多个 Target，
                # 而 calculate_strategy_signals 会内部调用 update_v_fit 追加历史。
                # 为了防止同一个时间点被重复追加多次导致历史数据污染（趋势变 0），
                # 我们在每档判定完后回滚掉这次追加。
                if len(state.v_fit_history) > 0:
                    state.v_fit_history.pop()

                if signal == 'BUY':
                    trades.append({
                        "bj_time": ts_str,
                        "local_time": state.local_time,
                        "v_fit": f"{meta.get('v_fit', 0):.2f}",
                        "target": target,
                        "ask": ask_price,
                        "reason": reason
                    })
        
        # 循环结束后，针对当前 tick “正式”追加一次 V_fit 历史（取最后一档计算的值即可，因为 weather 是一样的）
        # 这样能保证进入下一个 timestamp 时，历史是干净且连续的。
        v_fit = None
        # 核心物理拟合重构
        if state.om_curr is not None and state.mn_curr is not None:
             v_fit = state.om_curr * cfg.W1_OM + state.mn_curr * cfg.W2_MN + cfg.BIAS
             state.update_v_fit(v_fit)

    # 4. 输出汇总
    print(f"\n[回测结论]")
    print(f"分析样本点: {len(df)} | 发现买入机会: {len(trades)}")
    print("-" * 80)
    
    if trades:
        print(f"{'本地时间':10} | {'合约档位':8} | {'V_fit':6} | {'Ask报价':7} | {'判定理由'}")
        for t in trades: # 显示全量记录
            print(f"{t['local_time']:10} | {t['target']:<8.1f} | {t['v_fit']:6} | {t['ask']:<7} | {t['reason']}")
    else:
        print("[!] 该数据段内未发现符合买入条件的合约。")
    print("-" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="天气策略全市场扫描回测引擎")
    parser.add_argument("file", help="录制好的 CSV 数据路径")
    parser.add_argument("--preset", choices=["seoul", "london"], help="使用预置配置 (主要是设置时区)")
    
    args = parser.parse_args()
    run_backtest(args.file, args.preset)
