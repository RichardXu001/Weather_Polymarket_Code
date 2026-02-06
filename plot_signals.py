import pandas as pd
import matplotlib.pyplot as plt
import glob

def plot_signals_vs_price(file_pattern, col_name):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 选取一段有代表性的时间窗口（跨越 8C 的前后 2 小时）
    cross_time = pd.Timestamp("2026-02-05 23:24:35")
    start_time = cross_time - pd.Timedelta(hours=2)
    end_time = cross_time + pd.Timedelta(hours=2)
    mask = (df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)
    window = df.loc[mask].copy()

    fig, ax1 = plt.subplots(figsize=(14, 8))

    # 气温信号轴
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Temperature (°C)', color='black')
    ax1.plot(window['timestamp'], window['NO_ACTUAL'], label='NOAA (METAR)', color='red', linewidth=2, marker='o', markersize=4)
    ax1.plot(window['timestamp'], window['consensus_actual'], label='Consensus', color='blue', linestyle='--')
    ax1.plot(window['timestamp'], window['consensus_forecast'], label='Forecast (+1h)', color='green', alpha=0.5)
    ax1.axhline(y=8.0, color='gray', linestyle=':', label='Threshold 8.0°C')
    ax1.tick_params(axis='y', labelcolor='black')
    ax1.grid(True, which='both', linestyle='--', alpha=0.5)

    # 价格轴
    ax2 = ax1.twinx()
    ax2.set_ylabel('Market Price ($)', color='orange')
    ax2.step(window['timestamp'], window[col_name], label=f'Price ({col_name})', color='orange', where='post', linewidth=2)
    ax2.set_ylim(0, 1.05)
    ax2.tick_params(axis='y', labelcolor='orange')

    plt.title(f'Signal Analysis: Weather Sensors vs Market Price ({col_name})')
    fig.legend(loc='upper left', bbox_to_anchor=(0.1, 0.9))
    plt.tight_layout()
    
    plot_path = '/Users/liangxu/.gemini/antigravity/brain/90895f7a-8bf0-4dd2-85e9-54a6586cddaf/signals_vs_price.png'
    plt.savefig(plot_path)
    print(f"Plot saved to {plot_path}")

if __name__ == "__main__":
    plot_signals_vs_price('./data/server_data/weather_edge_EGLC_*.csv', '8°C')
