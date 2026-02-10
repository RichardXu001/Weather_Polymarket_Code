
import csv
import sys
import os
from datetime import datetime
from engine.config import QuantConfig
from engine.data_feed import WeatherState
from engine.strategy import StrategyKernel

# Initialize Config
config = QuantConfig()

def parse_float(val):
    try:
        return float(val)
    except:
        return None

def run_backtest(filename, city_name):
    print(f"\n--- Backtesting {city_name} with {filename} ---")
    
    if not os.path.exists(filename):
        print(f"File not found: {filename}")
        return

    # Track max temp just like the bot does
    daily_max_temp = -999.0
    has_traded = False
    
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        row_count = 0
        
        for row in reader:
            row_count += 1
            
            # 1. Reconstruct State
            try:
                ts = row['timestamp']
                local_time = row['local_time']
                local_hour = float(row['local_hour'])
                noaa_curr = parse_float(row.get('noaa_curr'))
            except Exception as e:
                continue

            # Update daily max
            if noaa_curr is not None:
                daily_max_temp = max(daily_max_temp, noaa_curr)

            state = WeatherState(
                timestamp=ts,
                local_time=local_time,
                local_hour=local_hour
            )
            state.noaa_curr = noaa_curr
            # For strategy compatibility
            state.noaa_now = noaa_curr
            
            # 2. Run Strategy
            signal, reason, target_temp = StrategyKernel.calculate_noaa_drop_signal(
                state, config, daily_max_temp if daily_max_temp > -900 else None, has_traded
            )
            
            # 3. Simulate Price Check (if signal triggered)
            if signal in ['BUY_DROP', 'BUY_FORCE']:
                target_contract = f"{int(target_temp)}°C" if target_temp is not None else "N/A"
                
                # Try to get price from CSV columns
                # Column format: "8°C_yes_ask"
                price_key = f"{target_contract}_yes_ask"
                price = parse_float(row.get(price_key))
                
                # Default "price" valid check
                executed = False
                skip_reason = ""
                
                if signal == 'BUY_FORCE':
                     if price is None or price <= 0.5:
                         signal = "WAIT_PV (Simulated)"
                         skip_reason = f"Price {price} <= 0.5"
                     else:
                         executed = True
                elif signal == 'BUY_DROP':
                    # Drop logic usually buys if price exists (and implicitly good due to drop)
                    # But pure drop logic doesn't have intrinsic price filter in strategy yet, 
                    # it relies on user finding a good entry.
                    if price is not None:
                        executed = True
                
                if executed or skip_reason:
                    print(f"[{local_time}] Signal: {signal} | Reason: {reason}")
                    print(f"    Current Temp: {noaa_curr} | Daily Max: {daily_max_temp}")
                    print(f"    Target Contract: {target_contract} (Should be Max: {daily_max_temp})")
                    print(f"    Price: {price} | Executed: {executed} | Skip: {skip_reason}")
                    
                    # Mark traded to stop further signals
                    has_traded = True
                    break # Stop after first trade for this day

    print(f"Finished. Max Temp was: {daily_max_temp}")

if __name__ == "__main__":
    # Test London (Starts 13:46 local, covers window)
    run_backtest("data/recordings/weather_recording_london_20260209_2146.csv", "London")
    
    # Test Ankara (Starts 16:36 local, covers end of window + force buy)
    run_backtest("data/recordings/weather_recording_ankara_20260209_2136.csv", "Ankara")
    
    # Test Seoul (Starts 14:08 local? Checking available files)
    # The 1308 file is small, but let's try it.
    run_backtest("data/recordings/weather_recording_seoul_20260209_1308.csv", "Seoul")
    
    # Test NYC (Starts 09:30 local, covers window)
    run_backtest("data/recordings/weather_recording_nyc_20260209_2230.csv", "NYC")
