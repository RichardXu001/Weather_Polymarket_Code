import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['STHeiti', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

def generate_fitting_report(file_path, output_image, output_csv):
    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 移除空值
    df = df.dropna(subset=['OM_ACTUAL', 'MN_ACTUAL', 'NO_ACTUAL'])
    
    # 使用回归得到的参数
    w1, w2, bias = 0.525, 0.450, 0.0003
    
    # 计算物理拟合值和最终取整预测值
    df['Physical_Value'] = w1 * df['OM_ACTUAL'] + w2 * df['MN_ACTUAL'] + bias
    df['Predicted_NOAA'] = np.floor(df['Physical_Value'] + 0.5)
    
    # 保存 CSV
    export_cols = ['timestamp', 'NO_ACTUAL', 'Predicted_NOAA', 'OM_ACTUAL', 'MN_ACTUAL', 'Physical_Value']
    df[export_cols].to_csv(output_csv, index=False)
    print(f"对比数据已导出至: {output_csv}")
    
    # 绘图
    plt.figure(figsize=(20, 10))
    
    # 绘制原始精细源供参考 (浅色)
    plt.plot(df['timestamp'], df['OM_ACTUAL'], label='OM (精细源)', color='green', alpha=0.3, linestyle='--')
    plt.plot(df['timestamp'], df['MN_ACTUAL'], label='MN (精细源)', color='blue', alpha=0.3, linestyle='--')
    
    # 绘制真实 NOAA (阶梯状)
    plt.step(df['timestamp'], df['NO_ACTUAL'], where='post', label='真实 NOAA', color='black', linewidth=2.5, alpha=0.8)
    
    # 绘制拟合后的 Predicted NOAA (红点或虚线)
    plt.step(df['timestamp'], df['Predicted_NOAA'], where='post', label='拟合预测 NOAA', color='red', linewidth=1, linestyle=':', alpha=0.9)

    # 装饰
    plt.title('NOAA 整数取整逻辑回归拟合对比图', fontsize=18)
    plt.xlabel('时间', fontsize=12)
    plt.ylabel('气温 (°C)', fontsize=14)
    plt.legend(loc='upper right', fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.4)
    
    # 时间轴格式化
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    
    plt.tight_layout()
    plt.savefig(output_image, dpi=300)
    print(f"拟合对比图已保存至: {output_image}")

if __name__ == "__main__":
    CSV_FILE = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_edge_EGLC_20260205_1514.csv"
    OUT_IMG = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/noaa_fitting_compare.png"
    OUT_CSV = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/noaa_fitting_data.csv"
    
    generate_fitting_report(CSV_FILE, OUT_IMG, OUT_CSV)
