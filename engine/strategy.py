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
        
        # 0. 准备工作 & 异常值剔除
        # -----------------------------------------------------------
        # 基准：NOAA (如果 NOAA 为空，无法进行偏差校验，退化为无校验)
        valid_om = state.om_now
        valid_mn = state.mn_now
        outlier_mode = False
        
        if state.noaa_now is not None:
             # 检查 Met.no 偏差
             if valid_mn is not None and abs(valid_mn - state.noaa_now) > config.MAX_SOURCE_DIVERGENCE:
                 # print(f"DEBUG: Excluding Met.no (Diff {abs(valid_mn - state.noaa_now):.2f} > {config.MAX_SOURCE_DIVERGENCE})")
                 valid_mn = None # 剔除
                 outlier_mode = True
            
             # 检查 Open-Meteo 偏差 (通常 Open-Meteo 较准，但也防万一)
             if valid_om is not None and abs(valid_om - state.noaa_now) > config.MAX_SOURCE_DIVERGENCE:
                 valid_om = None # 剔除
                 outlier_mode = True
        
        # 重新计算物理拟合值 (仅使用有效源)
        # 如果某个源被剔除，权重逻辑需要调整：
        # 这里简化处理：如果只剩一个源，直接用该源 + bias (忽略原权重比例，因为权重是针对二者共存设计的)
        if valid_om is not None and valid_mn is not None:
            # 正常双源
            v_fit = WeatherModel.calculate_v_fit(valid_om, valid_mn, config.W1_OM, config.W2_MN, config.BIAS)
        elif valid_om is not None:
            # 仅剩 Open-Meteo
            v_fit = valid_om + config.BIAS
        elif valid_mn is not None:
            # 仅剩 Met.no
            v_fit = valid_mn + config.BIAS
        else:
            # 全被剔除或无数据
            return 'IDLE', "No valid data sources", {}
            
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
        total_drops = 0  # 累计总下跌次数 (跨源求和)
        
        # Open-Meteo
        if valid_om is not None:
             if WeatherModel.get_trend(state.om_history) == -1: active_sources += 1
             total_drops += WeatherModel.get_drop_count(state.om_history)
             
        # Met.no
        if valid_mn is not None:
             if WeatherModel.get_trend(state.mn_history) == -1: active_sources += 1
             total_drops += WeatherModel.get_drop_count(state.mn_history)
             
        # NOAA (始终参与计数，它是真理)
        noaa_drop = (WeatherModel.get_trend(state.noaa_history) == -1)
        if noaa_drop: active_sources += 1
        total_drops += WeatherModel.get_drop_count(state.noaa_history)
        
        # 核心逻辑切换：只要物理总量 V_fit 在跌
        if v_fit_trend != -1:
            return 'IDLE', "V_fit not dropping", {"v_fit": v_fit, "trend": v_fit_trend}
            
        # 共振检查 1: 活跃源数量 (active_sources)
        required_resonance = 1 if outlier_mode else config.MIN_RESONANCE_SOURCES
        if active_sources < required_resonance:
            return 'IDLE', f"Insufficient active sources ({active_sources}/{required_resonance})", {"v_fit": v_fit, "sources": active_sources}
            
        # 共振检查 2: 总下跌次数 (Total Drops) - 用户核心需求
        # 如果剔除了一源，剩下的源必须贡献足够的下跌步数 (例如 OM 跌3次 + NOAA 跌0次 >= 3)
        if total_drops < config.TOTAL_REQUIRED_DROPS:
            return 'IDLE', f"Insufficient total drops ({total_drops}/{config.TOTAL_REQUIRED_DROPS})", {"v_fit": v_fit, "drops": total_drops}

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

    @staticmethod
    def calculate_noaa_drop_signal(
        state: WeatherState,
        config: QuantConfig,
        daily_max_temp: float,
        has_traded: bool,
        forecast_guard: dict = None,
    ):
        """
        增强版 NOAA 动态下跌策略 (V4.2.2 - 三阶段参数化 + 开关集成)
        """
        version = "V4.2.2+FGV2"

        if forecast_guard and forecast_guard.get("enabled"):
            if forecast_guard.get("locked"):
                fg_reason = forecast_guard.get("reason", "ForecastGuard locked")
                return "WAIT", f"ForecastGuard LOCKED ({fg_reason})", None
        
        if has_traded:
            return 'IDLE', f"Already traded today ({version})", None
            
        if state.noaa_now is None:
            return 'IDLE', "NOAA data missing", None
            
        h = state.local_hour
        
        # 1. 强买触发 (仅在 FORCE_BUY_TIME 后的 3 分钟内触发，避免全天候挂机启动推送)
        effective_max = max(state.noaa_now, daily_max_temp) if daily_max_temp is not None else state.noaa_now
        if h >= config.FORCE_BUY_TIME and h < (config.FORCE_BUY_TIME + 0.05):
            return 'BUY_FORCE', f"Force buy at {state.local_time} ({version})", effective_max


            
        if h < config.PEAK_TRIGGER_START:
            return 'IDLE', f"Before trigger window ({state.local_time})", None

        # --- A. 异常源隔离 ---
        valid_sources = {'noaa': state.noaa_now}
        if state.om_now is not None: valid_sources['om'] = state.om_now
        if state.mn_now is not None: valid_sources['mn'] = state.mn_now
        
        trusted_sources = []
        if config.OUTLIER_DETECTION_ENABLED:
            source_values = [v for v in valid_sources.values() if v is not None]
            for name, val in valid_sources.items():
                if val is None: continue
                others = [v for v in source_values if v != val]
                if not others: trusted_sources.append(name); continue
                min_diff = min([abs(val - o) for o in others])
                if min_diff <= config.OUTLIER_THRESHOLD: 
                    trusted_sources.append(name)
        else:
            # 如果关闭检测，所有非空源均被视为“可信”
            trusted_sources = [name for name, val in valid_sources.items() if val is not None]
        
        if 'noaa' not in trusted_sources and state.noaa_now is not None:
            return 'WAIT', f"NOAA outlier or filtered ({version})", None

        # --- B. 确定阶段阈值 (V4.2 Phase-based Logic) ---
        p1, p2, p3 = config.P1_START, config.P2_START, config.P3_START
        force = config.FORCE_BUY_TIME
        
        if h >= p3 and h < force:
            phase_name, req_resonance, req_duration, depth, noaa_req = "Phase3", config.P3_RES, config.P3_DUR, config.P3_DEPTH, config.P3_NOAA_REQ
        elif h >= p2 and h < p3:
            phase_name, req_resonance, req_duration, depth, noaa_req = "Phase2", config.P2_RES, config.P2_DUR, config.P2_DEPTH, config.P2_NOAA_REQ
        elif h >= p1 and h < p2:
            phase_name, req_resonance, req_duration, depth, noaa_req = "Phase1", config.P1_RES, config.P1_DUR, config.P1_DEPTH, config.P1_NOAA_REQ
        else:
            return 'WAIT', f"Outside defined phases ({state.local_time})", None

        # --- C. 计算共振 (Resonance) ---
        resonance = 0
        noaa_dropped = False
        
        # NOAA 判定 (使用全局 daily_max_temp)
        if 'noaa' in trusted_sources and daily_max_temp is not None:
            if state.noaa_now <= (daily_max_temp - depth + 0.001): 
                resonance += 1
                noaa_dropped = True
                
        # OM 判定 (使用各自的源最高温)
        if 'om' in trusted_sources and state.max_temp_om > -900:
            if state.om_now <= (state.max_temp_om - depth + 0.001): 
                resonance += 1
                
        # MN 判定 (使用各自的源最高温)
        if 'mn' in trusted_sources and state.max_temp_mn > -900:
            if state.mn_now <= (state.max_temp_mn - depth + 0.001): 
                resonance += 1

        # --- D. 综合判定 ---
        # 增加 NOAA 必选校验
        if noaa_req and not noaa_dropped:
            return 'WAIT', f"Monitoring {phase_name} (Res:{resonance}, Depth:{depth}, NOAA Drop REQ failed)", None

        if resonance >= req_resonance:
            # 在 Phase 3 (16:00后 灵敏期)，只要达标即视为趋势稳定（无需步进）
            is_p3 = (phase_name == "Phase3")
            if is_p3 or state.drop_count >= req_duration:
                return 'BUY_DROP', f"V4.2.2 {phase_name} Triggered (Res:{resonance}/{req_resonance}, Dur:{state.drop_count}/{req_duration}, Depth:{depth})", daily_max_temp
            else:
                return 'WAIT', f"{phase_name} Trend stabilizing (Res:{resonance}/{req_resonance}, Dur:{state.drop_count}/{req_duration})", None
        else:
            return 'WAIT', f"Monitoring {phase_name} (Res:{resonance}/{req_resonance}, Depth:{depth})", None
