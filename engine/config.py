import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class QuantConfig:
    """系统全局量化参数管理器"""
    
    # 1. 回归模型权重
    W1_OM = float(os.getenv("STRATEGY_W1_OM", 0.525))
    W2_MN = float(os.getenv("STRATEGY_W2_MN", 0.450))
    BIAS = float(os.getenv("STRATEGY_BIAS", 0.0))
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", "V3.0") # "V2.1" 或 "V3.0"
    
    # 1.1 异常值剔除阈值
    MAX_SOURCE_DIVERGENCE = float(os.getenv("STRATEGY_MAX_SOURCE_DIVERGENCE", 1.5))
    
    # 2. 核心判定约束
    AVOIDANCE_WIDTH = float(os.getenv("STRATEGY_AVOIDANCE_WIDTH", 0.3))
    REQUIRE_FORECAST_DROP = os.getenv("STRATEGY_REQUIRE_FORECAST_DROP", "true").lower() == "true"
    
    # 3. 多源共振逻辑
    MIN_RESONANCE_SOURCES = int(os.getenv("STRATEGY_MIN_RESONANCE_SOURCES", 2))
    MIN_DROPS_PER_SOURCE = int(os.getenv("STRATEGY_MIN_DROPS_PER_SOURCE", 1))
    TOTAL_REQUIRED_DROPS = int(os.getenv("STRATEGY_TOTAL_REQUIRED_DROPS", 2))
    REQUIRE_NOAA_DROP = os.getenv("STRATEGY_REQUIRE_NOAA_DROP", "false").lower() == "true"
    
    # 4. 时间窗口 (站点本地小时)
    PEAK_HOUR_START = float(os.getenv("STRATEGY_PEAK_HOUR_START", 0.0)) # 录制不限时，默认0
    PEAK_HOUR_END = float(os.getenv("STRATEGY_PEAK_HOUR_END", 24.0))
    STATION_TZ_OFFSET = int(os.getenv("STRATEGY_STATION_TZ_OFFSET", 0)) # 伦敦: 0, 首尔: 9
    
    # 4.1 交易触发时间窗口
    PEAK_TRIGGER_START = float(os.getenv("STRATEGY_PEAK_TRIGGER_START", 14.0)) # 14点开始监控下跌
    FORCE_BUY_TIME = float(os.getenv("STRATEGY_FORCE_BUY_TIME", 17.0))     # 17点强制买入
    TRADE_SHARES = float(os.getenv("STRATEGY_TRADE_SHARES", 5.0))         # 买入份额
    MIN_DROP_DURATION = int(os.getenv("STRATEGY_MIN_DROP_DURATION", 3))   # 连续下跌采样门槛
    
    # 5. 反弹保护
    REBOUND_THRESHOLD = float(os.getenv("STRATEGY_REBOUND_THRESHOLD", 0.1))
    
    # 6. 基础设施与采样频率 (V4.1)
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    
    # 单源采样间隔 (秒)
    INTERVAL_NOAA = int(os.getenv("STRATEGY_INTERVAL_NOAA", 30))
    INTERVAL_OM = int(os.getenv("STRATEGY_INTERVAL_OM", 60))
    INTERVAL_MN = int(os.getenv("STRATEGY_INTERVAL_MN", 60))
    INTERVAL_POLY = int(os.getenv("STRATEGY_INTERVAL_POLY", 30))
    
    # 7. 三阶段策略参数 (V4.2)
    # Phase 1
    P1_START = float(os.getenv("STRATEGY_P1_START", 14.0))
    P1_RES = int(os.getenv("STRATEGY_P1_RES", 2))
    P1_DUR = int(os.getenv("STRATEGY_P1_DUR", 3))
    P1_DEPTH = float(os.getenv("STRATEGY_P1_DEPTH", 0.3))
    P1_NOAA_REQ = os.getenv("STRATEGY_P1_NOAA_REQ", "true").lower() == "true"
    
    # Phase 2
    P2_START = float(os.getenv("STRATEGY_P2_START", 15.0))
    P2_RES = int(os.getenv("STRATEGY_P2_RES", 1))
    P2_DUR = int(os.getenv("STRATEGY_P2_DUR", 3))
    P2_DEPTH = float(os.getenv("STRATEGY_P2_DEPTH", 0.3))
    P2_NOAA_REQ = os.getenv("STRATEGY_P2_NOAA_REQ", "false").lower() == "true"
    
    # Phase 3
    P3_START = float(os.getenv("STRATEGY_P3_START", 16.0))
    P3_RES = int(os.getenv("STRATEGY_P3_RES", 1))
    P3_DUR = int(os.getenv("STRATEGY_P3_DUR", 1))
    P3_DEPTH = float(os.getenv("STRATEGY_P3_DEPTH", 0.3))
    P3_NOAA_REQ = os.getenv("STRATEGY_P3_NOAA_REQ", "false").lower() == "true"

    # 异常检测与隔离
    OUTLIER_DETECTION_ENABLED = os.getenv("STRATEGY_OUTLIER_DETECTION_ENABLED", "false").lower() == "true"
    OUTLIER_THRESHOLD = float(os.getenv("STRATEGY_OUTLIER_THRESHOLD", 1.5))

    # 运行配置
    # 运行配置 (支持 ACTIVE_LOCATIONS 列表 或 独立开关 ENABLE_{CITY})
    _active_raw = os.getenv("ACTIVE_LOCATIONS", "london,seoul").split(",")
    ACTIVE_LOCATIONS = []
    
    # 逻辑适配：如果设置了独立开关则优先，否则走列表
    _potential_cities = ["london", "seoul", "new_york", "ankara"]
    for city in _potential_cities:
        # 兼容映射：NEW_YORK 对应 nyc
        env_key = f"ENABLE_{city.upper()}"
        internal_key = "nyc" if city == "new_york" else city
        
        env_val = os.getenv(env_key)
        if env_val is not None:
            if env_val.lower() == "true":
                ACTIVE_LOCATIONS.append(internal_key)
        elif internal_key in _active_raw:
            ACTIVE_LOCATIONS.append(internal_key)

    # 8. Forecast Guard V2 (30min recalculation + risk lock)
    FORECAST_GUARD_ENABLED = os.getenv("FORECAST_GUARD_ENABLED", "true").lower() == "true"
    FORECAST_GUARD_FAIL_SAFE = os.getenv("FORECAST_GUARD_FAIL_SAFE", "true").lower() == "true"
    FORECAST_GUARD_RECALC_INTERVAL_SECONDS = int(os.getenv("FORECAST_GUARD_RECALC_INTERVAL_SECONDS", 1800))
    FORECAST_GUARD_RISK_SOURCE_THRESHOLD = int(os.getenv("FORECAST_GUARD_RISK_SOURCE_THRESHOLD", 1))

    # Risk conditions
    FORECAST_GUARD_NEAR_DELTA_C = float(os.getenv("FORECAST_GUARD_NEAR_DELTA_C", 1.5))
    FORECAST_GUARD_NEW_HIGH_DELTA_C = float(os.getenv("FORECAST_GUARD_NEW_HIGH_DELTA_C", 0.5))
    FORECAST_GUARD_REBOUND_DELTA_3H_C = float(os.getenv("FORECAST_GUARD_REBOUND_DELTA_3H_C", 0.8))

    # Unlock conditions
    FORECAST_GUARD_PEAK_PASSED_MINUTES = int(os.getenv("FORECAST_GUARD_PEAK_PASSED_MINUTES", 30))
    FORECAST_GUARD_UNLOCK_NOAA_DROP_C = float(os.getenv("FORECAST_GUARD_UNLOCK_NOAA_DROP_C", 0.3))
    FORECAST_GUARD_UNLOCK_AUX_DROP_C = float(os.getenv("FORECAST_GUARD_UNLOCK_AUX_DROP_C", 0.2))
    FORECAST_GUARD_UNLOCK_FUTURE_WARMING_C = float(
        os.getenv("FORECAST_GUARD_UNLOCK_FUTURE_WARMING_C", 0.2)
    )
    # [NEW] 极值时间感知策略相关
    FORECAST_GUARD_PEAK_THRESHOLD_C = float(os.getenv("FORECAST_GUARD_PEAK_THRESHOLD_C", 1.5))
    FORECAST_GUARD_PEAK_PROMINENCE_C = float(os.getenv("FORECAST_GUARD_PEAK_PROMINENCE_C", 0.3))

    # Met Office site-specific (global point hourly API)
    METOFFICE_SITE_SPECIFIC_API_KEY = os.getenv("METOFFICE_SITE_SPECIFIC_API_KEY", "")
    METOFFICE_SITE_SPECIFIC_BASE_URL = os.getenv(
        "METOFFICE_SITE_SPECIFIC_BASE_URL",
        "https://gateway.api-management.metoffice.cloud",
    )
    METOFFICE_SITE_SPECIFIC_CONTEXT = os.getenv("METOFFICE_SITE_SPECIFIC_CONTEXT", "/sitespecific/v0")
    METOFFICE_SITE_SPECIFIC_INTERVAL_SECONDS = int(os.getenv("METOFFICE_SITE_SPECIFIC_INTERVAL_SECONDS", 1800))
    
    @classmethod
    def to_dict(cls):
        """返回所有配置的字典，用于日志记录"""
        return {k: v for k, v in cls.__dict__.items() if not k.startswith("__") and not callable(v)}

if __name__ == "__main__":
    print("--- 当前加载的策略参数 ---")
    for k, v in QuantConfig.to_dict().items():
        print(f"{k}: {v}")
