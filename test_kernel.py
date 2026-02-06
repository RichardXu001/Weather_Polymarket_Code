from engine.strategy import StrategyKernel
from engine.data_feed import WeatherState
from engine.config import QuantConfig
import time

def test_downward_trend_strategy():
    print("=== 开始测试：向下跳变捕捉逻辑 (8.5 临界点) ===")
    
    # 覆盖默认配置用于测试
    cfg = QuantConfig
    cfg.TOTAL_REQUIRED_DROPS = 2 # 降低门槛方便测试
    
    # 模拟环境：伦敦本地 14:00 (符合窗口)
    state = WeatherState(
        timestamp="2026-02-06 14:00:00",
        local_hour=14.0,
        forecast_1h=8.0, 
        actual_now=9.0
    )
    
    # 模拟一个采样序列
    # 采样点 1: 8.9 -> 8.8 (符合共振，但在避让区边缘)
    state.om_history = [9.0, 8.9, 8.8]
    state.mn_history = [9.1, 9.0, 8.9] 
    state.om_now = 8.8
    state.mn_now = 8.9
    
    signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
    print(f"[采样 8.85°C] 信号: {signal:<10} 原因: {reason}")

    # 采样点 2: 8.6 (符合共振，进入 [8.5, 8.8] 避让区)
    state.om_history.append(8.6)
    state.mn_history.append(8.7)
    state.om_now = 8.6
    state.mn_now = 8.7
    signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
    print(f"[采样 8.65°C] 信号: {signal:<10} 原因: {reason}")

    # 采样点 3: 8.4 (越过 8.5 点，解除避让)
    state.om_history.append(8.4)
    state.mn_history.append(8.4)
    state.om_now = 8.4
    state.mn_now = 8.4
    signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, cfg)
    print(f"[采样 8.40°C] 信号: {signal:<10} 原因: {reason}")
    if signal == 'BUY':
        print(f">>> 验证通过: 成功在 {meta['v_fit']:.2f}°C 脱离避让区并触发买入！")

if __name__ == "__main__":
    test_downward_trend_strategy()
