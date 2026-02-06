import pandas as pd
import glob

def analyze_full_dynamics(file_pattern):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 锁定关键时间：NOAA 首次报出 8.0 的时刻
    noaa_8_time = df[df['NO_ACTUAL'] >= 8.0].iloc[0]['timestamp']
    
    # 分析此后 8 小时的动态
    mask = (df['timestamp'] >= noaa_8_time) & (df['timestamp'] <= noaa_8_time + pd.Timedelta(hours=8))
    window = df.loc[mask]
    
    print(f"--- NOAA 报出 8.0°C 后的市场全景 (从 {noaa_8_time} 开始) ---")
    # 打印关键列：时间、实测、预报、8度价格、9度价格
    print(window[['timestamp', 'NO_ACTUAL', 'consensus_actual', 'consensus_forecast', '8°C', '9°C']].iloc[::40].to_string(index=False))

if __name__ == "__main__":
    analyze_full_dynamics('./data/server_data/weather_edge_EGLC_*.csv')
