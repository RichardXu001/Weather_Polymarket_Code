import pandas as pd
import matplotlib.pyplot as plt
import glob
import os

def analyze_data(file_pattern, title):
    files = glob.glob(file_pattern)
    if not files:
        print(f"No files found for {file_pattern}")
        return
    
    dfs = []
    for f in files:
        df = pd.read_csv(f)
        dfs.append(df)
    
    df = pd.concat(dfs).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    print(f"--- Analysis for {title} ---")
    print(df.describe())
    
    # Identify price columns (they usually have °C in the name)
    price_cols = [c for c in df.columns if '°C' in c]
    
    # Plot consensus_actual and prices
    fig, ax1 = plt.subplots(figsize=(12, 6))
    
    ax1.set_xlabel('Timestamp')
    ax1.set_ylabel('Consensus Actual / Forecast', color='tab:red')
    ax1.plot(df['timestamp'], df['consensus_actual'], label='Consensus Actual', color='tab:red', marker='.')
    ax1.plot(df['timestamp'], df['consensus_forecast'], label='Consensus Forecast', color='orange', linestyle='--')
    ax1.tick_params(axis='y', labelcolor='tab:red')
    
    ax2 = ax1.twinx()
    ax2.set_ylabel('Market Prices (Odds)', color='tab:blue')
    for col in price_cols:
        ax2.plot(df['timestamp'], df[col], label=col, alpha=0.7)
    ax2.tick_params(axis='y', labelcolor='tab:blue')
    
    plt.title(f'Weather vs Market Prices - {title}')
    fig.tight_layout()
    plt.legend(loc='upper left', bbox_to_anchor=(1.1, 1))
    
    # Save statistics to a file instead of showing plot
    stats_file = f'analysis_{title.lower().replace(" ", "_")}.txt'
    with open(stats_file, 'w') as f:
        f.write(f"Analysis for {title}\n")
        f.write(df.describe().to_string())
        f.write("\n\nCorrelation between Consensus Actual and Prices:\n")
        f.write(df[['consensus_actual'] + price_cols].corr()['consensus_actual'].to_string())

    print(f"Stats saved to {stats_file}")

if __name__ == "__main__":
    # Analyze London (EGLC)
    analyze_data('./data/server_data/weather_edge_EGLC_*.csv', 'London EGLC')
    # Analyze Seoul
    analyze_data('./data/server_data/weather_edge_20260205*.csv', 'Seoul')
    # Also check local files for Seoul just in case
    analyze_data('weather_edge_20260205*.csv', 'Seoul Local')
