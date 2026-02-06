import pandas as pd
import glob

def find_settlement_time(file_pattern, col_name):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    print(f"--- {col_name} 价格演变分析 ---")
    
    # 找到价格首次达到 1.0 的时间
    max_price = df[col_name].max()
    print(f"最高价格: {max_price}")
    
    hit_1 = df[df[col_name] >= 0.99]
    if not hit_1.empty:
        print(f"首次达到 $1.0 (或 >$0.99) 的时间: {hit_1.iloc[0]['timestamp']}")
    else:
        print("价格从未达到 $1.0")
        
    # 查看最后 5 条记录的价格
    print("\n最后 5 条数据记录:")
    print(df[['timestamp', 'NO_ACTUAL', col_name]].tail(5).to_string(index=False))

if __name__ == "__main__":
    find_settlement_time('./data/server_data/weather_edge_EGLC_*.csv', '8°C')
    find_settlement_time('./data/server_data/weather_edge_EGLC_*.csv', '9°C')
    find_settlement_time('./data/server_data/weather_edge_EGLC_*.csv', '7°C')
