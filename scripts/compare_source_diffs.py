import pandas as pd
import numpy as np

def analyze_source_differences(file_path):
    df = pd.read_csv(file_path)
    
    # 1. 定义精细源平均值
    df['Fine_Avg'] = (df['OM_ACTUAL'] + df['MN_ACTUAL']) / 2
    
    # 2. 计算相对于 NOAA 的差值 (Delta)
    df['Diff_OM_NOAA'] = df['OM_ACTUAL'] - df['NO_ACTUAL']
    df['Diff_MN_NOAA'] = df['MN_ACTUAL'] - df['NO_ACTUAL']
    df['Diff_FineAvg_NOAA'] = df['Fine_Avg'] - df['NO_ACTUAL']
    
    # 3. 统计描述
    stats = df[['Diff_OM_NOAA', 'Diff_MN_NOAA', 'Diff_FineAvg_NOAA']].describe(percentiles=[.05, .25, .5, .75, .95])
    
    print("--- NOAA 与 各精细源差值范围分析 (单位: °C) ---")
    print(stats)
    
    # 4. 计算变动的一致性 (Correlation)
    correlation = df[['NO_ACTUAL', 'OM_ACTUAL', 'MN_ACTUAL']].corr()
    print("\n--- 各来源相关性矩阵 ---")
    print(correlation)
    
    # 5. 识别极端差值 (可能代表 NOAA 更新严重滞后)
    max_diff_idx = df['Diff_FineAvg_NOAA'].idxmax()
    min_diff_idx = df['Diff_FineAvg_NOAA'].idxmin()
    
    print(f"\n--- 极端差值案例 ---")
    print(f"最大正向差值: {df.loc[max_diff_idx, 'Diff_FineAvg_NOAA']:.2f}°C (发生时间: {df.loc[max_diff_idx, 'timestamp']})")
    print(f"最大负向差值: {df.loc[min_diff_idx, 'Diff_FineAvg_NOAA']:.2f}°C (发生时间: {df.loc[min_diff_idx, 'timestamp']})")
    
    # 6. 计算差值的波动范围 (Range)
    diff_range = stats.loc['max'] - stats.loc['min']
    print("\n--- 差值波动全量范围 (Max - Min) ---")
    print(diff_range)

if __name__ == "__main__":
    CSV_FILE = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_edge_EGLC_20260205_1514.csv"
    analyze_source_differences(CSV_FILE)
