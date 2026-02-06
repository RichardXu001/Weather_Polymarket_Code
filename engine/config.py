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
    
    # 2. 核心判定约束
    AVOIDANCE_WIDTH = float(os.getenv("STRATEGY_AVOIDANCE_WIDTH", 0.3))
    REQUIRE_FORECAST_DROP = os.getenv("STRATEGY_REQUIRE_FORECAST_DROP", "true").lower() == "true"
    
    # 3. 多源共振逻辑
    MIN_RESONANCE_SOURCES = int(os.getenv("STRATEGY_MIN_RESONANCE_SOURCES", 2))
    MIN_DROPS_PER_SOURCE = int(os.getenv("STRATEGY_MIN_DROPS_PER_SOURCE", 1))
    TOTAL_REQUIRED_DROPS = int(os.getenv("STRATEGY_TOTAL_REQUIRED_DROPS", 2))
    REQUIRE_NOAA_DROP = os.getenv("STRATEGY_REQUIRE_NOAA_DROP", "false").lower() == "true"
    
    # 4. 时间窗口 (站点本地小时)
    PEAK_HOUR_START = float(os.getenv("STRATEGY_PEAK_HOUR_START", 12.0))
    PEAK_HOUR_END = float(os.getenv("STRATEGY_PEAK_HOUR_END", 18.0))
    STATION_TZ_OFFSET = int(os.getenv("STRATEGY_STATION_TZ_OFFSET", 0)) # 伦敦: 0, 首尔: 9
    
    # 5. 反弹保护
    REBOUND_THRESHOLD = float(os.getenv("STRATEGY_REBOUND_THRESHOLD", 0.1))
    
    # 6. 基础设施配置
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    
    @classmethod
    def to_dict(cls):
        """返回所有配置的字典，用于日志记录"""
        return {k: v for k, v in cls.__dict__.items() if not k.startswith("__") and not callable(v)}

if __name__ == "__main__":
    print("--- 当前加载的策略参数 ---")
    for k, v in QuantConfig.to_dict().items():
        print(f"{k}: {v}")
