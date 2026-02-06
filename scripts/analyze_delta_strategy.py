import pandas as pd
from datetime import datetime, timedelta

def analyze_strategy_data(file_path):
    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 转换为伦敦时间 (UTC+0)，方便进行时段分析 (北京时间 - 8h)
    df['timestamp_lon'] = df['timestamp'] - timedelta(hours=8)
    df['hour_lon'] = df['timestamp_lon'].dt.hour
    
    # 计算 Delta: 来源实测 - 共识实测 (衡量各来源相对于总体的偏移)
    df['delta_OM'] = df['OM_ACTUAL'] - df['consensus_actual']
    df['delta_MN'] = df['MN_ACTUAL'] - df['consensus_actual']
    df['delta_NO'] = df['NO_ACTUAL'] - df['consensus_actual']
    
    # 计算 实测 vs 预测 的趋势偏差 (衡量预测的超前/滞后)
    df['error_forecast'] = df['consensus_forecast'] - df['consensus_actual']
    
    # 按时段统计 (例如：凌晨、上午、下午、深夜)
    # 伦敦时间 0-6 (凌晨), 6-12 (上午), 12-18 (下午), 18-24 (晚上)
    def get_period(h):
        if 0 <= h < 6: return '1_凌晨'
        if 6 <= h < 12: return '2_上午'
        if 12 <= h < 18: return '3_下午'
        return '4_晚上'
    
    df['period'] = df['hour_lon'].apply(get_period)
    
    # 计算精细数据源 (OM/MN) 的平均偏移
    df['delta_精细_avg'] = (df['delta_OM'] + df['delta_MN']) / 2
    
    analysis = df.groupby('period').agg({
        'delta_NO': 'mean',
        'delta_精细_avg': 'mean',
        'error_forecast': 'mean',
        'consensus_actual': 'std'  # 波动率
    }).round(4)
    
    print("--- 各时段偏差与趋势分析 (伦敦时间时段) ---")
    print(analysis)
    
    # 计算最新的趋势偏差
    latest = df.iloc[-1]
    print(f"\n最新记录 (北京时间 {latest['timestamp']}):")
    print(f"预测误差 (Forecast - Actual): {latest['error_forecast']:.2f}")
    print(f"精细来源平均 Delta: {latest['delta_精细_avg']:.2f}")

if __name__ == "__main__":
    CSV_FILE = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_edge_EGLC_20260205_1514.csv"
    analyze_strategy_data(CSV_FILE)
