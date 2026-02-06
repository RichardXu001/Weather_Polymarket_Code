import pandas as pd
import numpy as np
from scipy.optimize import minimize

def analyze_noaa_regression(file_path):
    df = pd.read_csv(file_path)
    
    # 移除包含 NaN 的行
    df = df.dropna(subset=['OM_ACTUAL', 'MN_ACTUAL', 'NO_ACTUAL'])
    
    X = df[['OM_ACTUAL', 'MN_ACTUAL']].values
    Y = df['NO_ACTUAL'].values

    def objective(params):
        w1, w2, bias = params
        x_linear = w1 * X[:, 0] + w2 * X[:, 1] + bias
        # 这里模拟 Round 取整
        y_pred = np.floor(x_linear + 0.5) 
        return np.sum((y_pred - Y)**2)

    initial_params = [0.5, 0.5, 0.0]
    res = minimize(objective, initial_params, method='Nelder-Mead', tol=1e-6)
    
    w1, w2, bias = res.x
    
    # 计算拟合准确率 (百分比)
    x_est = w1 * X[:, 0] + w2 * X[:, 1] + bias
    y_final_pred = np.floor(x_est + 0.5)
    accuracy = np.mean(y_final_pred == Y) * 100
    
    print("--- NOAA 取整回归模型反推结果 ---")
    print(f"权重 OM (w1): {w1:.4f}")
    print(f"权重 MN (w2): {w2:.4f}")
    print(f"核心偏移量 (Bias): {bias:.4f}")
    print(f"模型匹配率 (Accuracy): {accuracy:.2f}%")
    
    # 物理意义解释
    print("\n[模型解释]")
    print(f"NOAA 的底层物理估算公式近似为: Round({w1:.2f}*OM + {w2:.2f}*MN + ({bias:.2f}))")
    print(f"这意味着当精细平均值到达 {8.0-bias:.2f}°C 时，NOAA 极大概率跳变到 8°C。")

if __name__ == "__main__":
    CSV_FILE = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/server_data/weather_edge_EGLC_20260205_1514.csv"
    analyze_noaa_regression(CSV_FILE)
