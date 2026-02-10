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
    # 1. 加载配置
    presets = load_presets()
    conf = presets.get(preset_name, {})
    tz_offset = conf.get("tz_offset", 0)
    cfg = QuantConfig
    cfg.STATION_TZ_OFFSET = tz_offset
    
    print(f"[*] 启动全市场扫描回测 | 时区偏移: {tz_offset}")
    print("-" * 80)

    # 2. 读取数据与识别合约
    import glob
    all_files = sorted(glob.glob(csv_path))
    if not all_files:
        print(f"[错误] 未找到匹配的数据文件: {csv_path}")
        return
    
    if len(all_files) > 1:
        print(f"[*] 检测到多文件模式，正在合并 {len(all_files)} 个片段...")
        dfs = [pd.read_csv(f) for f in all_files]
        df = pd.concat(dfs, ignore_index=True)
        # 核心：时序重组与去重
        df.sort_values(by='timestamp', inplace=True)
        df.drop_duplicates(subset=['timestamp'], keep='first', inplace=True)
        df.reset_index(drop=True, inplace=True)
        print(f"[*] 合并完成。总行数: {len(df)}")
    else:
        df = pd.read_csv(all_files[0])
    
    # 识别 Yes 报价列 (例如 -4°C_yes 或 price_-4°C_yes)
    price_cols = [c for c in df.columns if '_yes' in c]
    if not price_cols:
        # 兜底兼容旧格式
        price_cols = [c for c in df.columns if c.startswith('price_') or '°C' in c]
        
    target_map = {c: extract_target_from_col(c) for c in price_cols}
    active_targets = {c: t for c, t in target_map.items() if t is not None}
    
    print(f"[*] 识别到 {len(active_targets)} 档合约选项: {sorted(list(active_targets.values()))}")
    
    state = WeatherState(timestamp="", local_time="", local_hour=0.0)
    trades = []
    
    # 3. 循环仿真 (NOAA Drop Strategy 适配版)
    # 我们需要模拟每一天的 max_temp 动态变化
    daily_max_temp = -999.0
    has_traded_today = False
    
    unique_dates = set()
    
    for idx, row in df.iterrows():
        ts_str = row['timestamp']
        # 状态更新
        state.timestamp = ts_str
        state.local_time = row.get('local_time', '--:--')
        
        # 兼容性时间计算
        try:
            # 优先从 CSV 读取记录的本地时间，避免回测环境时区偏移
            if 'local_hour' in row and pd.notna(row['local_hour']):
                state.local_hour = float(row['local_hour'])
            if 'local_time' in row and pd.notna(row['local_time']):
                state.local_time = str(row['local_time'])
            else:
                # 兜底：从 timestamp 计算
                ts = datetime.strptime(str(row['timestamp']), '%Y-%m-%d %H:%M:%S')
                state.local_hour = (ts.hour + (tz_offset - 8)) % 24 + ts.minute / 60.0
                state.local_time = f"{int(state.local_hour):02d}:{int((state.local_hour % 1) * 60):02d}"
        except:
            state.local_hour = float(row.get('local_hour', 0))
            state.local_time = str(row.get('local_time', "00:00"))

        # 映射原始天气数据 (兼容新旧字段名，增加前向填充逻辑以提高鲁棒性)
        new_noaa = row.get('noaa_curr') if 'noaa_curr' in row and row.get('noaa_curr') else row.get('NO_ACTUAL')
        new_om = row.get('om_curr') if 'om_curr' in row and row.get('om_curr') else row.get('OM_ACTUAL')
        new_mn = row.get('mn_curr') if 'mn_curr' in row and row.get('mn_curr') else row.get('MN_ACTUAL')

        # 转换为 float，如果缺失则沿用 state 中的上一次值 (Forward Fill)
        try: 
            val = float(new_noaa) if new_noaa is not None and str(new_noaa).strip() != "" else None
            if val is not None: state.noaa_curr = val
        except: pass
        
        try:
            val = float(new_om) if new_om is not None and str(new_om).strip() != "" else None
            if val is not None: state.om_curr = val
        except: pass
            
        try:
            val = float(new_mn) if new_mn is not None and str(new_mn).strip() != "" else None
            if val is not None: state.mn_curr = val
        except: pass
            
        # 更新每日最高温
        if state.noaa_curr is not None:
             daily_max_temp = max(daily_max_temp, state.noaa_curr)
             state.max_temp_overall = daily_max_temp
        if state.om_curr is not None:
             state.max_temp_om = max(state.max_temp_om, state.om_curr)
        if state.mn_curr is not None:
             state.max_temp_mn = max(state.max_temp_mn, state.mn_curr)

        # 维护连续下跌计数 (以 NOAA 为核心)
        if state.noaa_curr is not None and state.max_temp_overall > -900:
            if state.noaa_curr < state.max_temp_overall:
                state.drop_count += 1
            else:
                state.drop_count = 0
        
        # 兼容旧代码
        state.noaa_now = state.noaa_curr
        state.om_now = state.om_curr
        state.mn_now = state.mn_curr

        # --- 策略执行 ---
        # 1. 重置每日交易锁 (简单的日期检测)
        # 假设文件包含多天数据，或者单天。这里简单起见，如果 local_hour 突然变小(跨天)，重置
        signal, reason, target_temp = StrategyKernel.calculate_noaa_drop_signal(
            state, cfg, daily_max_temp if daily_max_temp > -900 else None, has_traded_today
        )
        if signal in ['BUY_DROP', 'BUY_FORCE']:
            # 模拟执行
            executed = False
            skip_reason = ""
            
            # [Unit Conversion] 
            unit = conf.get("unit", "C")
            display_temp = target_temp
            symbol = "°C"
            if unit == "F" and target_temp is not None:
                display_temp = round(target_temp * 1.8 + 32)
                symbol = "°F"
            
            target_contract_prefix = f"{int(display_temp)}{symbol}"
            
            # 智能搜索合约 (处理 NYC 的范围合约，如 "24-25°F")
            target_contract = target_contract_prefix # 默认
            price = None
            
            # 从所有列名中提取“基准标题”（以 _yes_ask 结尾的列）
            available_titles = [c.replace('_yes_ask', '') for c in df.columns if '_yes_ask' in c]
            
            if available_titles:
                found = False
                for title in available_titles:
                    # 1. 精确/包含匹配
                    if target_contract_prefix in title:
                        target_contract = title
                        found = True
                        break
                    
                    # 2. 华氏度范围匹配 (NYC 专用)
                    if unit == 'F':
                        import re
                        # 匹配 "X-Y°F"
                        range_match = re.search(r'(\d+)-(\d+)°F', title)
                        if range_match:
                            low, high = int(range_match.group(1)), int(range_match.group(2))
                            if low <= display_temp <= high:
                                target_contract = title
                                found = True
                                break
                        # 匹配 "X°F or below"
                        below_match = re.search(r'(\d+)°F or below', title)
                        if below_match and display_temp <= int(below_match.group(1)):
                            target_contract = title
                            found = True
                            break
                        # 匹配 "X°F or higher"
                        higher_match = re.search(r'(\d+)°F or higher', title)
                        if higher_match and display_temp >= int(higher_match.group(1)):
                            target_contract = title
                            found = True
                            break
            
            p_col = f"{target_contract}_yes_ask"
            if p_col in df.columns:
                try: 
                    price = float(row.get(p_col))
                except: pass
            
            # [Rule] 价格滤网 (Unified)
            if price is None or price <= 0.5:
                skip_reason = f"Price {price} <= 0.5"
                # 记录一次跳过的“交易”以便分析
                trades.append({
                    "local_time": state.local_time,
                    "type": f"SKIP_{signal.split('_')[1]}", # SKIP_FORCE or SKIP_DROP
                    "target": target_contract,
                    "price": price,
                    "reason": f"{reason} | {skip_reason}"
                })
                has_traded_today = True # 锁定
            else:
                executed = True
            
            if executed:
                trades.append({
                    "local_time": state.local_time,
                    "type": signal,
                    "target": target_contract,
                    "price": price,
                    "reason": reason
                })
                has_traded_today = True

    # 4. 输出汇总与报告导出
    export_backtest_report(csv_path, preset_name, df, trades, daily_max_temp)

def export_backtest_report(csv_path, preset_name, df, trades, daily_max_temp):
    """
    生成并打印格式化的回测报告 (Markdown 风格)
    """
    city = (preset_name or "Unknown").upper()
    timestamp_report = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filename = os.path.basename(csv_path)
    
    report = []
    report.append(f"# 天气策略回测报告 ({city})")
    report.append(f"- **生成时间**: {timestamp_report}")
    report.append(f"- **数据源文件**: `{filename}`")
    report.append(f"- **采样总点数**: {len(df)}")
    
    final_peak = daily_max_temp if daily_max_temp > -900 else "N/A"
    report.append(f"- **全天记录最高温**: {final_peak}")
    report.append(f"- **触发交易总数**: {len(trades)}")
    report.append("\n## 交易执行详情")
    
    if not trades:
        report.append("> [!NOTE]\n> 本次回测未识别到任何符合条件的交易信号。")
    else:
        report.append("| 本地时间 | 信号类型 | 交易合约 | 报价(USDC) | 详细判定原因 (Reasoning) |")
        report.append("| :--- | :--- | :--- | :--- | :--- |")
        for t in trades:
            # 强化 Markdown 兼容性
            price_str = f"{t['price']:.3f}" if t['price'] is not None else "N/A"
            report.append(f"| {t['local_time']} | `{t['type']}` | {t['target']} | {price_str} | {t['reason']} |")
    
    report_content = "\n".join(report)
    
    # 打印到控制台
    print("\n" + "="*20 + " 结构化回测报告 " + "="*20)
    print(report_content)
    print("="*56 + "\n")
    
    # 可选：保存到文件
    report_dir = "backtest_reports"
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    
    safe_filename = filename.replace(".csv", ".md")
    report_path = os.path.join(report_dir, f"report_{city}_{safe_filename}")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"[*] 报告已导出至: {report_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="天气策略 (NOAA Drop) 回测引擎")
    parser.get_default = lambda name: None # Mock for config
    parser.add_argument("file", help="录制好的 CSV 数据路径")
    parser.add_argument("--preset", help="使用预置配置 (主要是设置时区)")
    parser.add_argument("--version", choices=["V2.1", "V3.0"], default="V3.0", help="指定策略版本")
    
    args = parser.parse_args()
    
    # 自动加载环境变量以确保 QuantConfig 拿到最新参数
    from dotenv import load_dotenv
    load_dotenv()
    
    run_backtest(args.file, args.preset)
