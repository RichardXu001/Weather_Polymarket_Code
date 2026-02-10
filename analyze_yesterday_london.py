
import csv
import os
from datetime import datetime
from dotenv import load_dotenv
import numpy as np

# Mocking parts of the engine to run locally without complex imports
class QuantConfig:
    def __init__(self):
        load_dotenv()
        self.W1_OM = float(os.getenv("STRATEGY_W1_OM", 0.525))
        self.W2_MN = float(os.getenv("STRATEGY_W2_MN", 0.450))
        self.BIAS = float(os.getenv("STRATEGY_BIAS", 0.0))
        self.MAX_SOURCE_DIVERGENCE = float(os.getenv("STRATEGY_MAX_SOURCE_DIVERGENCE", 1.5))
        self.AVOIDANCE_WIDTH = float(os.getenv("STRATEGY_AVOIDANCE_WIDTH", 0.3))
        self.REQUIRE_FORECAST_DROP = os.getenv("STRATEGY_REQUIRE_FORECAST_DROP", "true").lower() == "true"
        self.MIN_RESONANCE_SOURCES = int(os.getenv("STRATEGY_MIN_RESONANCE_SOURCES", 2))
        self.TOTAL_REQUIRED_DROPS = int(os.getenv("STRATEGY_TOTAL_REQUIRED_DROPS", 3))
        self.REQUIRE_NOAA_DROP = os.getenv("STRATEGY_REQUIRE_NOAA_DROP", "false").lower() == "true"
        self.PEAK_HOUR_START = float(os.getenv("STRATEGY_PEAK_HOUR_START", 12.0))
        self.PEAK_HOUR_END = float(os.getenv("STRATEGY_PEAK_HOUR_END", 18.0))

class WeatherState:
    def __init__(self):
        self.noaa_history = []
        self.om_history = []
        self.mn_history = []
        self.v_fit_history = []
        self.noaa_now = None
        self.om_now = None
        self.mn_now = None
        self.actual_now = None
        self.forecast_1h = None
        self.local_hour = 0
        self.target_temp = 8.0

    def update(self, noaa, om, mn, actual, forecast, hour):
        self.noaa_now = noaa
        self.om_now = om
        self.mn_now = mn
        self.actual_now = actual
        self.forecast_1h = forecast
        self.local_hour = hour
        
        if noaa is not None: self.noaa_history.append(noaa)
        if om is not None: self.om_history.append(om)
        if mn is not None: self.mn_history.append(mn)
        
        for h in [self.noaa_history, self.om_history, self.mn_history]:
            if len(h) > 10: h.pop(0)

    def update_v_fit(self, v_fit):
        self.v_fit_history.append(v_fit)
        if len(self.v_fit_history) > 10: self.v_fit_history.pop(0)

class WeatherModel:
    @staticmethod
    def calculate_v_fit(om, mn, w1, w2, bias):
        if om is None or mn is None: return None
        return om * w1 + mn * w2 + bias
    
    @staticmethod
    def get_trend(values, min_net_drop=0.01):
        if len(values) < 3: return 0
        net_change = values[-1] - values[0]
        drops = sum(1 for i in range(1, len(values)) if values[i] < values[i-1])
        rises = sum(1 for i in range(1, len(values)) if values[i] > values[i-1])
        if net_change <= -min_net_drop and drops > rises: return -1
        if net_change >= min_net_drop and rises > drops: return 1
        return 0

    @staticmethod
    def get_drop_count(values):
        if len(values) < 2: return 0
        return sum(1 for i in range(1, len(values)) if values[i] < values[i-1])

def analyze_strategy(state, config):
    valid_om = state.om_now
    valid_mn = state.mn_now
    outlier_mode = False
    
    if state.noaa_now is not None:
        if valid_mn is not None and abs(valid_mn - state.noaa_now) > config.MAX_SOURCE_DIVERGENCE:
            valid_mn = None
            outlier_mode = True
        if valid_om is not None and abs(valid_om - state.noaa_now) > config.MAX_SOURCE_DIVERGENCE:
            valid_om = None
            outlier_mode = True
            
    if valid_om is not None and valid_mn is not None:
        v_fit = WeatherModel.calculate_v_fit(valid_om, valid_mn, config.W1_OM, config.W2_MN, config.BIAS)
    elif valid_om is not None:
        v_fit = valid_om + config.BIAS
    elif valid_mn is not None:
        v_fit = valid_mn + config.BIAS
    else:
        return 'IDLE', "No valid data sources", {}
    
    if v_fit is None: return 'IDLE', "Data incomplete", {}
    state.update_v_fit(v_fit)
    
    # 1. Peak Hour
    if not (config.PEAK_HOUR_START <= state.local_hour <= config.PEAK_HOUR_END):
        return 'IDLE', f"Outside peak hours ({state.local_hour:.2f})", {"v_fit": v_fit}
        
    # 2. Forecast Drop
    if config.REQUIRE_FORECAST_DROP:
        if state.forecast_1h is not None and state.actual_now is not None:
            if state.forecast_1h > state.actual_now:
                return 'WAIT', f"Forecast is rising ({state.forecast_1h} > {state.actual_now})", {"v_fit": v_fit}

    # 3. Trend Analysis
    if len(state.v_fit_history) < 3:
        return 'IDLE', "Building history (V_fit)", {"v_fit": v_fit}
        
    v_fit_trend = WeatherModel.get_trend(state.v_fit_history)
    active_sources = 0
    total_drops = 0
    
    if valid_om is not None:
        if WeatherModel.get_trend(state.om_history) == -1: active_sources += 1
        total_drops += WeatherModel.get_drop_count(state.om_history)
    if valid_mn is not None:
        if WeatherModel.get_trend(state.mn_history) == -1: active_sources += 1
        total_drops += WeatherModel.get_drop_count(state.mn_history)
    
    noaa_drop = (WeatherModel.get_trend(state.noaa_history) == -1)
    if noaa_drop: active_sources += 1
    total_drops += WeatherModel.get_drop_count(state.noaa_history)
    
    if v_fit_trend != -1:
        return 'IDLE', f"V_fit not dropping (trend={v_fit_trend})", {"v_fit": v_fit}
        
    required_resonance = 1 if outlier_mode else config.MIN_RESONANCE_SOURCES
    if active_sources < required_resonance:
        return 'IDLE', f"Insufficient active sources ({active_sources}/{required_resonance})", {"v_fit": v_fit}
        
    if total_drops < config.TOTAL_REQUIRED_DROPS:
        return 'IDLE', f"Insufficient total drops ({total_drops}/{config.TOTAL_REQUIRED_DROPS})", {"v_fit": v_fit}

    if config.REQUIRE_NOAA_DROP and not noaa_drop:
        return 'WAIT', "NOAA not dropping (required)", {"v_fit": v_fit}

    # 4. Avoidance Zone
    jump_point = round(v_fit + 0.5) - 0.5
    if v_fit >= jump_point:
        if v_fit < jump_point + config.AVOIDANCE_WIDTH:
            return 'WAIT', f"In avoidance zone ({v_fit:.2f} near {jump_point})", {"v_fit": v_fit}

    return 'BUY', "All signals aligned", {"v_fit": v_fit}

def main():
    config = QuantConfig()
    state = WeatherState()
    csv_path = "data/weather_edge_london_yesterday_full.csv"
    
    print(f"--- Strategy Analysis for Yesterday (London) ---")
    print(f"Config: Peak {config.PEAK_HOUR_START}-{config.PEAK_HOUR_END}, Min Resonance {config.MIN_RESONANCE_SOURCES}, Total Drops {config.TOTAL_REQUIRED_DROPS}")
    
    buys = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row['timestamp']
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            hour = dt.hour + dt.minute / 60.0
            
            try:
                noaa = float(row['NO_ACTUAL']) if row['NO_ACTUAL'] else None
                om = float(row['OM_ACTUAL']) if row['OM_ACTUAL'] else None
                mn = float(row['MN_ACTUAL']) if row['MN_ACTUAL'] else None
                actual = float(row['consensus_actual']) if row['consensus_actual'] else None
                forecast = float(row['consensus_forecast']) if row['consensus_forecast'] else None
            except:
                continue

            state.update(noaa, om, mn, actual, forecast, hour)
            signal, reason, meta = analyze_strategy(state, config)
            
            v_fit_trend = meta.get('trend', 0)
            v_fit = meta.get('v_fit', 0)

            if signal == 'BUY':
                buys.append((ts, reason, v_fit))
                print(f"[{ts}] TRIGGERED! Reason: {reason} | V_fit: {v_fit:.2f}")
            elif dt.hour >= 12 and dt.hour < 18:
                # 寻找下跌趋势但被其他条件挡住的情况
                if v_fit_trend == -1:
                    print(f"[{ts}] DOWN TREND! {signal:5} | V_fit: {v_fit:.2f} | Reason: {reason}")
                elif dt.minute % 20 == 0 and dt.second < 30:
                     print(f"[{ts}] {signal:5} | V_fit: {v_fit:.2f} | Reason: {reason}")
    
    if not buys:
        print("\nNo BUY signals triggered yesterday.")
        # Let's find some 'almost' signals
        print("\nReviewing status around peak hour drops...")
        # (Could add more specific debugging here)

if __name__ == "__main__":
    main()
