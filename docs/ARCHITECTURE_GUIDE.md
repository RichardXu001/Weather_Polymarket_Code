# 天气量化交易系统：架构设计文档 (v1.1)

## 1. 系统概览
本系统旨在通过自动化方式，利用多源天气数据源与物理拟合模型，在 Polymarket 天气预测市场上执行高胜率的量化对冲。

## 2. 核心架构组件

### 2.1 数据网关 (Data Gateway)
- **输入源**：NOAA (METAR), Open-Meteo (OM), Met.no (MN), Polymarket Gamma API。
- **职责**：定时轮询，将异构数据标准化。

### 2.2 物理建模中台 (Physical Modeling Engine)
- **核心计算**：依据 OM 和 MN 数据计算 `V_fit`（物理估算值），为策略提供趋势预判。

### 2.3 策略决策机 (Strategy Decision Maker)
- **判定管道 (Pipeline)**：
    1. **本地时区过滤器**：根据站点 `tz_offset` 验证是否处于本地交易时段。
    2. **预测值硬约束**：验证 1h Forecast 是否支持趋势。
    3. **总量共振分析**：基于 `V_fit` 历史序列进行趋势确认。
    4. **非对称避让逻辑**：执行 [X.5, X.5 + 0.3] 风险评估。

### 2.4 地点元数据库 (Location Metadata) [NEW]
- **配置文件**：`locations.json`。
- **职责**：通过 ICAO 关联站点经纬度及 **本地时区偏移量**。实现全球站点的“即插即用”。

## 3. 核心逻辑时序
1. `Tick` -> `TradingHub` 根据 Preset 自动识别时区。
2. `Modeling` 更新 `V_fit` 历史窗口。
3. `Decision` 根据 **站点本地时间** 进行窗口拦截。
4. 满足 `V_fit` 下跌 + 活跃源共振 -> 触发 `BUY`。

## 4. 关键文件布局
```text
project/
├── .env                  # 策略核心阈值配置
├── locations.json        # 站点元数据库
├── trading_hub.py        # 地点自适应入口 (实盘主程序)
├── backtest_engine.py    # 多合约扫描回测引擎 (V2.0)
├── engine/               # 核心引擎目录
│   ├── strategy.py       # StrategyKernel (实盘/回测共用)
│   └── models.py         # WeatherModel (V_fit 计算 + predict_noaa)
└── executor/             # 下单执行目录
```

## 5. 回测引擎特性 (V2.0)
- **多合约扫描**: 自动识别 CSV 中所有温度合约
- **精准匹配**: 仅推荐 `predict_noaa(v_fit) == target` 的合约
- **逻辑复用**: 100% 调用 `StrategyKernel`，与实盘代码完全一致
