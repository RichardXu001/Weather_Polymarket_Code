import pandas as pd
import glob

def analyze_competition(file_pattern):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 选取气温跨越 8C 后的时间段
    cross_time = pd.Timestamp("2026-02-05 23:24:35")
    mask = (df['timestamp'] >= cross_time) & (df['timestamp'] <= cross_time + pd.Timedelta(hours=4))
    window = df.loc[mask]
    
    print("--- 跨越 8°C 后的价格博弈 (23:24 - 03:24) ---")
    print(window[['timestamp', 'NO_ACTUAL', '8°C', '9°C']].iloc[::30].to_string(index=False)) # 每30行采样一次

if __name__ == "__main__":
    analyze_competition('./data/server_data/weather_edge_EGLC_*.csv')
