import numpy as np

class WeatherModel:
    """物理还原与拟合模型引擎"""
    
    @staticmethod
    def calculate_v_fit(om_actual, mn_actual, w1, w2, bias=0.0):
        """
        根据回归公式计算物理估算值 V_fit
        V_fit = w1 * OM + w2 * MN + bias
        """
        if om_actual is None or mn_actual is None:
            return None
        return w1 * om_actual + w2 * mn_actual + bias
    
    @staticmethod
    def predict_noaa(v_fit):
        """
        对物理拟合值执行标准四舍五入，预测 NOAA 的输出
        """
        if v_fit is None:
            return None
        return int(np.floor(v_fit + 0.5))

    @staticmethod
    def get_trend(values, min_net_drop=0.01):
        """
        判断趋势方向 (优化版：允许持平，只要整体净值下降)
        返回: 
            1: 整体上升 (Net > 0)
           -1: 整体下降 (Net < 0 且无显著回升)
            0: 波动或持平
        """
        if len(values) < 3:
            return 0
        
        net_change = values[-1] - values[0]
        
        # 统计步长中的动作
        drops = 0
        rises = 0
        for i in range(1, len(values)):
            if values[i] < values[i-1]:
                drops += 1
            elif values[i] > values[i-1]:
                rises += 1
        
        # 核心逻辑：
        # 1. 净跌幅必须存在
        # 2. 下跌步数必须显著多于上涨步数
        if net_change <= -min_net_drop and drops > rises:
            return -1
        if net_change >= min_net_drop and rises > drops:
            return 1
            
        return 0

    @staticmethod
    def get_drop_count(values):
        """计算序列中的下跌步数"""
        if len(values) < 2:
            return 0
        drops = 0
        for i in range(1, len(values)):
            if values[i] < values[i-1]:
                drops += 1
        return drops

