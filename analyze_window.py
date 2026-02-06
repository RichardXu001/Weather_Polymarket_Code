import pandas as pd
import glob

def detail_arbitrage_window(file_pattern, threshold, col_name):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # First hit
    cross_indices = df[df['consensus_actual'] >= threshold].index
    if len(cross_indices) == 0: return
    
    target_idx = cross_indices[0]
    # Get range around it (e.g., 10 rows before, 20 rows after)
    start_idx = max(0, target_idx - 10)
    end_idx = min(len(df), target_idx + 20)
    
    window = df.iloc[start_idx:end_idx]
    print(window[['timestamp', 'consensus_actual', 'NO_ACTUAL', col_name]].to_string(index=False))

if __name__ == "__main__":
    print("--- London Crossing Window Detail ---")
    detail_arbitrage_window('./data/server_data/weather_edge_EGLC_*.csv', 8.0, '8°C')
