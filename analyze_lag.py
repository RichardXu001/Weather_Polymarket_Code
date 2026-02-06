import pandas as pd
import glob

def analyze_lead_lag(file_pattern, title):
    files = glob.glob(file_pattern)
    if not files: return
    df = pd.concat([pd.read_csv(f) for f in files]).sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # price columns
    price_cols = [c for c in df.columns if '°C' in c]
    
    # Calculate correlation between forecast and future actuals
    # shifted actuals (1 hour later, assuming 60 mins)
    df = df.set_index('timestamp')
    df_resampled = df.resample('1min').ffill()
    
    # Look for lag between consensus_actual and market prices
    metrics = []
    for col in price_cols:
        # Correlation at various lags (0 to 10 mins)
        lags = {}
        for lag in range(0, 11):
            corr = df_resampled['consensus_actual'].corr(df_resampled[col].shift(-lag))
            lags[lag] = corr
        best_lag = max(lags, key=lambda k: abs(lags[k]))
        metrics.append({
            'column': col,
            'best_lag_mins': best_lag,
            'max_corr': lags[best_lag],
            'current_corr': lags[0]
        })
    
    # Look for forecast accuracy
    # consensus_forecast vs consensus_actual shifted 1 hour (60 mins)
    forecast_corr = df_resampled['consensus_forecast'].corr(df_resampled['consensus_actual'].shift(-60))
    
    print(f"\n--- Refined Analysis for {title} ---")
    print(f"Forecast (+1h) Correlation with future Actual (+1h): {forecast_corr:.4f}")
    print("Market Price Lags relative to Consensus Actual:")
    print(pd.DataFrame(metrics))

if __name__ == "__main__":
    analyze_lead_lag('./data/server_data/weather_edge_EGLC_*.csv', 'London EGLC')
    analyze_lead_lag('weather_edge_20260205*.csv', 'Seoul Local')
