import pandas as pd
import glob

def analyze_noaa_frequency(file_pattern):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 找出 NOAA (METAR) 变化的时刻
    noaa_changes = df[df['NO_ACTUAL'].diff() != 0].copy()
    noaa_changes['interval'] = noaa_changes['timestamp'].diff()
    
    print("--- NOAA (METAR) 更新频率分析 ---")
    print(noaa_changes[['timestamp', 'NO_ACTUAL', 'interval']].dropna())
    print(f"\n平均更新间隔: {noaa_changes['interval'].mean()}")
    print(f"最小更新间隔: {noaa_changes['interval'].min()}")
    print(f"最大更新间隔: {noaa_changes['interval'].max()}")

if __name__ == "__main__":
    analyze_noaa_frequency('./data/server_data/weather_edge_EGLC_*.csv')
