import pandas as pd
import glob

def calculate_arbitrage_profit(file_pattern, threshold, col_name):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Find the moment it crosses the threshold
    # For London, the winning bracket was 8C (meaning 8.0-8.9 or similar)
    cross_point = df[df['consensus_actual'] >= threshold].iloc[0] if not df[df['consensus_actual'] >= threshold].empty else None
    
    if cross_point is not None:
        price_at_cross = cross_point[col_name]
        time_at_cross = cross_point['timestamp']
        print(f"Moment consensus_actual hit {threshold}: {time_at_cross}")
        print(f"Price for '{col_name}' at that moment: ${price_at_cross}")
        print(f"Theoretical profit per share (if it closes in this bracket): ${1.0 - float(price_at_cross):.4f}")
        
        # Look 10 mins later
        target_time = time_at_cross + pd.Timedelta(minutes=10)
        future_data = df[df['timestamp'] >= target_time]
        if not future_data.empty:
            price_10m = future_data.iloc[0][col_name]
            print(f"Price 10 minutes later: ${price_10m}")
            print(f"Market adjustment during lag: ${float(price_10m) - float(price_at_cross):.4f}")
    else:
        print(f"Consensus actual never hit {threshold} in the provided data.")

if __name__ == "__main__":
    print("--- London EGLC Analysis (Threshold 8.0°C) ---")
    calculate_arbitrage_profit('./data/server_data/weather_edge_EGLC_*.csv', 8.0, '8°C')
