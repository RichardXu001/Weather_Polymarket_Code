from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class WeatherState:
    """即时天气市场状态快照"""
    
    # 基础信息
    timestamp: str
    # 基础信息
    timestamp: str      # 采样北京时间
    local_time: str     # 站点本地时间 (HH:MM)
    local_hour: float   # 站点本地小时 (用于逻辑判断)
    
    # 当前采样值 (原始数据)
    noaa_curr: Optional[float] = None
    om_curr: Optional[float] = None
    om_fore: Optional[float] = None
    mn_curr: Optional[float] = None
    mn_fore: Optional[float] = None

    @property
    def noaa_now(self): return self.noaa_curr
    @noaa_now.setter
    def noaa_now(self, val): self.noaa_curr = val

    @property
    def om_now(self): return self.om_curr
    @om_now.setter
    def om_now(self, val): self.om_curr = val

    @property
    def mn_now(self): return self.mn_curr
    @mn_now.setter
    def mn_now(self, val): self.mn_curr = val
    
    consensus_curr: Optional[float] = None  # 计算出的共识实测值
    consensus_fore: Optional[float] = None  # 计算出的共识预测值
    
    # 合约市场 (记录所有选项)
    market_prices: dict = field(default_factory=dict) # { "Under 8.0": 0.5, "Over 8.0": 0.5 }
    
    # 历史序列 (用于趋势分析)
    noaa_history: List[float] = field(default_factory=list)
    om_history: List[float] = field(default_factory=list)
    mn_history: List[float] = field(default_factory=list)
    v_fit_history: List[float] = field(default_factory=list)

    # 结果追踪
    max_temp_overall: float = -999.0     # 追踪当天官方源 (NOAA) 的最高实测温
    max_temp_om: float = -999.0          # 追踪当天 Open-Meteo 的最高实测温
    max_temp_mn: float = -999.0          # 追踪当天 Met.no 的最高实测温
    drop_count: int = 0                  # 连续满足下跌条件的采样次数
    has_traded_today: bool = False       # 追踪当天是否已经执行过买入

    def update_v_fit(self, v_fit: float):
        """记录最新的拟合值"""
        self.v_fit_history.append(v_fit)
        # 保持窗口长度，例如保留最近 10 个点
        if len(self.v_fit_history) > 10:
            self.v_fit_history.pop(0)
