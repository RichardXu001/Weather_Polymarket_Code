import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import os

# 设置中文字体支持 (macOS 常用字体)
plt.rcParams['font.sans-serif'] = ['STHeiti', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

def visualize_weather_data(file_path, output_image):
    # 加载数据
    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # --- 关键修改：过滤时间，截止到 2月6日 08:00 ---
    end_time = datetime(2026, 2, 6, 8, 0, 0)
    df = df[df['timestamp'] <= end_time]
    
    # 计算平均值
    df['OM_MN_AVG_ACTUAL'] = df[['OM_ACTUAL', 'MN_ACTUAL']].mean(axis=1)
    df['OM_MN_AVG_FORECAST'] = df[['OM_FORECAST', 'MN_FORECAST']].mean(axis=1)
    
    # --- 关键修改：放大画布尺寸 ---
    fig, ax1 = plt.subplots(figsize=(20, 10))
    ax2 = ax1.twinx()
    
    # --- 绘制左轴 (温度) ---
    ax1.plot(df['timestamp'], df['consensus_actual'], label='共识实际值', color='black', linewidth=2.5, marker='o', markersize=4, alpha=0.8)
    ax1.plot(df['timestamp'], df['consensus_forecast'], label='共识预测值', color='grey', linestyle='--', linewidth=2, alpha=0.7)
    
    ax1.plot(df['timestamp'], df['NO_ACTUAL'], label='NO 实际值', color='blue', alpha=0.4, linestyle=':')
    ax1.plot(df['timestamp'], df['OM_ACTUAL'], label='OM 实际值', color='green', alpha=0.4, linestyle=':')
    ax1.plot(df['timestamp'], df['MN_ACTUAL'], label='MN 实际值', color='red', alpha=0.4, linestyle=':')
    
    ax1.plot(df['timestamp'], df['OM_MN_AVG_ACTUAL'], label='OM/MN 平均实际值', color='orange', linewidth=3, alpha=0.9)
    ax1.plot(df['timestamp'], df['OM_MN_AVG_FORECAST'], label='OM/MN 平均预测值', color='orange', linestyle='--', linewidth=3, alpha=0.6)
    
    # --- 绘制右轴 (报价) ---
    ax2.plot(df['timestamp'], df['8°C'], label='8°C 报价', color='purple', linewidth=2, alpha=0.8)
    ax2.plot(df['timestamp'], df['9°C'], label='9°C 报价', color='brown', linewidth=2, alpha=0.8)
    
    # 装饰
    ax1.set_xlabel('时间', fontsize=12)
    ax1.set_ylabel('气温 (°C)', color='black', fontsize=14)
    ax2.set_ylabel('报价 (Ask Price)', color='purple', fontsize=14)
    
    plt.title(f'天气数据分析 (至 08:00): {os.path.basename(file_path)}', fontsize=18)
    
    # 合并图例并放到下方，避免遮挡曲线
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=5, fontsize=10)
    
    ax1.grid(True, which='both', linestyle='--', alpha=0.5)
    
    # 时间轴格式化
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=2)) # 每2小时一个主刻度
    plt.xticks(rotation=45)
    
    plt.tight_layout()
    
    # 保存图片
    plt.savefig(output_image, dpi=300, bbox_inches='tight')
    print(f"优化后的图表已保存至: {output_image}")

if __name__ == "__main__":
    CSV_FILE = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_edge_EGLC_20260205_1514.csv"
    OUTPUT_IMG = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_market_analysis.png"
    
    visualize_weather_data(CSV_FILE, OUTPUT_IMG)
