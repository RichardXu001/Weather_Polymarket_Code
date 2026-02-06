import pandas as pd
import numpy as np

def analyze_integer_risk(file_path):
    df = pd.read_csv(file_path)
    # NOAA 是整数，OM/MN 是精细值
    # 计算精细源平均值
    df['Fine_Avg'] = (df['OM_ACTUAL'] + df['MN_ACTUAL']) / 2
    
    # 计算精细源相对于 NOAA 的偏差 (Delta_NOAA)
    df['Delta_NOAA'] = df['Fine_Avg'] - df['NO_ACTUAL']
    
    # 识别“危险区”：精细源距离下一个或上一个整数的距离
    # 比如 Fine_Avg = 7.9, NOAA = 8.0, 距离极近，NOAA 可能随时跳变
    df['Dist_to_Int'] = np.abs(df['Fine_Avg'] - np.round(df['Fine_Avg']))
    
    # 统计数据
    print("--- NOAA 整数偏差分析 ---")
    print(f"Fine_Avg 与 NOAA 的平均偏差 (Mean Delta): {df['Delta_NOAA'].mean():.4f}")
    print(f"最大偏差 (Max Delta): {df['Delta_NOAA'].max():.4f}")
    print(f"最小偏差 (Min Delta): {df['Delta_NOAA'].min():.4f}")
    
    # 统计潜在跳变次数：当 Fine_Avg 明显超过 NOAA 且 NOAA 未更新时
    # 比如 Fine_Avg > 8.5 且 NOAA = 8.0
    critical_cases = df[np.abs(df['Delta_NOAA']) >= 0.5]
    print(f"\n潜在跳变风险点数 (Fine 与 NOAA 偏差 >= 0.5): {len(critical_cases)}")
    
    # 统计“安全区”：距离整数 > 0.3 的比例
    safe_zone = df[df['Dist_to_Int'] > 0.3]
    print(f"安全区样本占比 (Fine 距离整数 > 0.3): {len(safe_zone)/len(df)*100:.2f}%")

if __name__ == "__main__":
    CSV_FILE = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_edge_EGLC_20260205_1514.csv"
    analyze_integer_risk(CSV_FILE)
