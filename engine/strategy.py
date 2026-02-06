from .models import WeatherModel
from .data_feed import WeatherState
from .config import QuantConfig

class StrategyKernel:
    """统一决策内核 - 100% 对齐实盘与回测"""
    
    @staticmethod
    def calculate_strategy_signals(state: WeatherState, config: QuantConfig):
        """
        核心信号算法
        返回: (signal, reason, meta_data)
        signal: 'BUY', 'WAIT', 'IDLE'
        """
        
        # 0. 准备工作：计算物理拟合值
        v_fit = WeatherModel.calculate_v_fit(
            state.om_now, state.mn_now, 
            config.W1_OM, config.W2_MN, config.BIAS
        )
        if v_fit is None:
            return 'IDLE', "Data incomplete", {}
        
        state.update_v_fit(v_fit)
        
        # 1. 基础门槛：时间窗口约束
        if not (config.PEAK_HOUR_START <= state.local_hour <= config.PEAK_HOUR_END):
            return 'IDLE', f"Outside peak hours ({state.local_hour})", {"v_fit": v_fit}
            
        # 2. 基础门槛：预测方向约束
        if config.REQUIRE_FORECAST_DROP:
            if state.forecast_1h is not None and state.actual_now is not None:
                if state.forecast_1h > state.actual_now:
                    return 'WAIT', "Forecast is rising, blocking entry", {"v_fit": v_fit}

        # 3. 趋势驱动分析 (基于 V_fit 的总量共振)
        # 获取物理拟合值的历史趋势
        if len(state.v_fit_history) < 3:
            return 'IDLE', "Building history (V_fit)", {"v_fit": v_fit}
            
        v_fit_trend = WeatherModel.get_trend(state.v_fit_history)
        
        # 统计独立源作为辅助确认
        active_sources = 0
        if WeatherModel.get_trend(state.om_history) == -1: active_sources += 1
        if WeatherModel.get_trend(state.mn_history) == -1: active_sources += 1
        noaa_drop = (WeatherModel.get_trend(state.noaa_history) == -1)
        if noaa_drop: active_sources += 1
        
        # 核心逻辑切换：只要物理总量 V_fit 在跌，且满足基本的活跃源要求 (默认 1 个即可触发)
        if v_fit_trend != -1:
            return 'IDLE', "V_fit not dropping", {"v_fit": v_fit, "trend": v_fit_trend}
            
        if active_sources < config.MIN_RESONANCE_SOURCES:
            # 特殊情况：如果 V_fit 在跌但独立源检测不到（可能因为步长太小），我们作为辅助判断
            # 这里我们坚持至少有 MIN_RESONANCE_SOURCES 的活跃性，或者下调该配置
            return 'IDLE', "Insufficient active sources", {"v_fit": v_fit, "sources": active_sources}

        if config.REQUIRE_NOAA_DROP and not noaa_drop:
             return 'WAIT', "NOAA not dropping (required)", {"v_fit": v_fit}

        # 4. 非对称避让区逻辑 (安全门槛)
        # 我们关注 8.5, 7.5 这种跳变点
        jump_point = round(v_fit + 0.5) - 0.5 # 找到最近的 .5 点
        
        # 向下趋势下的避让：[X.5, X.5 + 0.3]
        if v_fit >= jump_point:
            # 处于跳转前的临界区
            if (v_fit < jump_point + config.AVOIDANCE_WIDTH):
                return 'WAIT', f"Within downward avoidance zone ({v_fit:.2f} near {jump_point})", {"v_fit": v_fit}
        else:
            # 已经跌破 X.5
            # 逻辑已兑现：PASS
            pass

        # 5. 最终确认：买入
        return 'BUY', "All signals aligned", {
            "v_fit": v_fit, 
            "jump_pred": WeatherModel.predict_noaa(v_fit),
            "resonance": active_sources
        }
