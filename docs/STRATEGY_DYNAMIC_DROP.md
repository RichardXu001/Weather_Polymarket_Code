# 动态 NOAA 下跌交易策略 (V4.2.2+FGV2)

## 1. 策略概览
本策略通过监控核心气象源（NOAA）与辅助源（Open-Meteo, Met.no）的实时跌幅，在气温见顶回落的瞬间抢占 Polymarket 胜算。当前实盘版本为 **V4.2.2+FGV2**：保留三阶段触发，同时增加 Forecast Guard V2 的前置风控锁。

## 2. 三阶段触发矩阵

系统通过 `.env` 参数驱动，根据站点本地时间自动切换逻辑模式。

| 阶段名称 | 时间窗口 (默认) | 触发条件 (Resonance) | 持续计数 (Duration) | 跌幅深度 (Depth) | NOAA 必选 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 1: 严谨期** | 14:00 - 15:00 | ≥ 2 源共振 | 3 次 (3min) | 0.3°C | **YES** |
| **Phase 2: 窗口期** | 15:00 - 16:00 | ≥ 1 源触发 | 3 次 (3min) | 0.3°C | NO |
| **Phase 3: 灵敏期** | 16:00 - 17:00 | ≥ 1 源触发 | **瞬时 (Skip count)** | 0.3°C | NO |

### 核心参数定义:
- **Resonance (共振)**: 跌幅达标的可信源数量（NOAA, OM, MN）。
- **Duration (持续)**: 连续满足下跌门槛的采样次数，用于过滤高频噪声。
- **Depth (深度)**: 当前温度相对于该源今日最高温的跌幅。

## 3. 关键判定增强

### 3.1 源锚点同步 (Source Anchoring)
每个数据源均独立追踪其今日最高温（如 `max_temp_om`），决策时对比该源自身的历史峰值，以消除不同气象站之间的绝对值系统偏差。

---

## 4. 多温标兼容性 (Celsius/Fahrenheit Support) [NEW]

系统通过 `locations.json` 中的 `unit` 字段自动适配不同地区的温标（如 NYC 使用华氏度 °F）。

### 4.1 自动温标转换
- **核心逻辑**: 策略内部统一使用摄氏度 (Celsius) 进行共振计算，以确保跨源算法的一致性。
- **执行映射**: 在下单触发阶段，系统会根据目标站点的 `unit` 设置，自动将触发阈值转换为对应温标（如 $F = C \times 1.8 + 32$）。

### 4.2 智能合约匹配 (Smart Matching)
针对美国市场（如 NYC）常见的复杂合约名，系统实现了正则语义匹配：
- **范围合约**: 自动识别并匹配 `24-25°F` 类型的中继合约。
- **边界合约**: 完美识别 `24°F or below` 或 `26°F or higher`。
- **单位映射**: 确保在执行买入时，`target_asset` 能够精准指向正确的离散合约档位。

## 5. 异常源隔离逻辑 (Outlier Isolation)

为了应对个别数据源发生设备故障或极端数据噪声，系统保留了异常隔离能力：
- **触发机制**: 可通过 `.env` 中的 `STRATEGY_OUTLIER_DETECTION_ENABLED` 开关实时控制。
- **隔离门槛**: 当配置开启时，若某源读数与其它两源的平均值偏差绝对值 **> 1.5°C**，则该源在当前采样点将被视为“不可信”，不参与共振计算。
- **默认状态**: 出于稳定性考虑，当前默认设置为 `false`（即全量采信），但在极端天气波动时建议开启。

## 6. 强制执行与防御

- **17:00 强制买入 (`BUY_FORCE`)**: 若整日均未触发下跌信号，则在 17:00 准点切入。
- **全线价格保护（可配置）**: 无论何种信号，若目标合约价格（Yes Ask） **< 0.9 USDC**（默认）则放弃交易并标记为 `SKIP_PRICE`。
- **独立审计存证**: 每一笔信号及其决策逻辑（Res/Dur/Depth）均持久化至 `data/trades/`。

### 6.1 Forecast Guard V2（30 分钟重算 + 风险锁）

为避免“下午假回落，夜间再冲高”事故，策略新增 Forecast Guard V2：

1. **重算频率**:
- 从本地 `12:00` 起，每 `30 分钟`重算一次风险（可通过 `.env` 调整）。

2. **基线校正**:
- 预报与实测存在系统偏差，先做校正：  
  `bias = 当前 NOAA 实测 - 当前预报值`  
  `校正预报 = 原预报 + bias`

3. **风险判定（按源独立判断）**:
- `AfternoonPeak`: 13:00-17:00 校正后预报峰值  
- `NightPeak`: 17:00-24:00 校正后预报峰值  
- 命中任一条件即该源判为 risky:
  - `NightPeak >= AfternoonPeak - 1.5°C`
  - `NightPeak >= DayMaxSoFar - 0.5°C`
  - `未来3小时最高温 - 当前温度 >= 0.8°C`

4. **风险锁动作**:
- 只要 `>=1` 个源判 risky，立即进入锁定状态：
  - 禁止 `BUY_DROP`
  - 禁止 `BUY_FORCE`

5. **解锁条件（全部满足才解锁）**:
- 时间已过预测夜间峰值 `+30分钟`
- 实测滤波确认降温：
  - NOAA 连续 3 次下降，累计降幅 `>=0.3°C`
  - 且 OM/MN 至少 1 个源连续 3 次下降，累计降幅 `>=0.2°C`
- 预报不再回暖：
  - 至少 2 个预报源满足“未来 2 小时最大升温 `<=0.2°C`”

6. **故障兜底**:
- 若 Forecast Guard 关键预报源全不可用，默认 fail-safe（锁定不买）。

## 7. 全球数据源汇总（当前可用）

截至 **2026-02-11**，当前策略可用的“全球性”数据源如下：

| 类别 | 数据源 | 覆盖范围 | 主要用途 | 当前状态 |
| :--- | :--- | :--- | :--- | :--- |
| 实测 | AviationWeather METAR | 全球机场站点 | 当前温度锚点（结算口径对齐） | 已在 4 地长期使用 |
| 预报 | Open-Meteo `ecmwf_ifs` | 全球经纬度 | 未来温度趋势（小时级输出） | 已验证 4 地可用 |
| 预报 | Open-Meteo `gfs_global` | 全球经纬度 | 未来温度趋势（小时级输出） | 已验证 4 地可用 |
| 预报 | MET Norway Locationforecast | 全球经纬度 | 未来温度趋势（1h/6h 混合） | 已验证 4 地可用 |
| 预报 | Met Office DataHub `site-specific` | 全球经纬度（按点位请求） | 高质量点位小时预报 | 已验证 4 地可用（含 Ankara） |

### 7.1 数量口径
- **全球预报源**: `4` 个（ECMWF/GFS/MET.no/MetOffice site-specific）
- **全球实测源**: `1` 个（METAR）
- **合计全球源**: `5` 个

### 7.2 现阶段推荐组合（Forecast Guard）
- 主组合：`MetOffice site-specific + ECMWF + GFS`
- 兜底组合：`MET.no + METAR 当前值锚点`
- 建议频率：免费档统一按 `1800s`（每 0.5 小时）刷新一次预报缓存。

## 8. 附录：本地强源 API 与 Key 获取

为实现“全球源优先 + 本地强源补强”，本策略额外维护以下本地源接入信息，便于后续快速启用：

### 8.1 NYC - NWS forecastHourly（官方小时预报）
- **是否需要 Key**: 不需要。
- **调用方式**: 先请求 `https://api.weather.gov/points/{lat},{lon}`，再读取返回中的 `forecastHourly` URL。
- **注意事项**: 建议设置明确的 `User-Agent`。
- **官方文档**: https://www.weather.gov/documentation/services-web-api

### 8.2 London - Met Office（DataPoint / DataHub）
- **DataPoint（历史免费方案）**:
  - 申请入口（历史）: https://www.metoffice.gov.uk/services/data/datapoint/api
  - 退役 FAQ: https://www.metoffice.gov.uk/services/data/datapoint/datapoint-retirement-faqs
- **DataHub（当前主通道）**:
  - 入口: https://datahub.metoffice.gov.uk/
  - FAQ: https://datahub.metoffice.gov.uk/support/faqs
- **当前脚本使用变量**:
  - `METOFFICE_DATAPOINT_API_KEY`
  - `METOFFICE_SITE_ID_LONDON`（可选，未设置时脚本会自动找最近站点）
- **DataHub Atmospheric Models（已验证可用）**:
  - Base URL: `https://gateway.api-management.metoffice.cloud`
  - Context: `/atmospheric-models/1.0.0`
  - 鉴权头: `apikey: <your_key>`
  - 建议频率: 免费档按 `1800s`（每 0.5 小时）抓取一次，避免超配额。
  - 当前环境变量:
    - `METOFFICE_DATAHUB_API_KEY`
    - `METOFFICE_DATAHUB_BASE_URL`
    - `METOFFICE_DATAHUB_CONTEXT`
    - `METOFFICE_DATAHUB_INTERVAL_SECONDS`

### 8.3 Seoul - KMA OpenAPI（超短临 + 短临）
- **是否需要 Key**: 需要 `serviceKey`。
- **申请入口**: https://www.data.go.kr/en/data/15084084/openapi.do
- **平台使用说明**: https://www.data.go.kr/ugs/selectPublicDataUseGuideView.do
- **当前脚本使用变量**:
  - `KMA_SERVICE_KEY`
  - `KMA_NX_SEOUL`（可选）
  - `KMA_NY_SEOUL`（可选）
  - 若未设置 `KMA_NX_SEOUL/KMA_NY_SEOUL`，脚本会按经纬度自动换算 DFS 网格。

### 8.4 建议的 .env 占位配置（先留空）
```env
# NYC (NWS 无需 key)

# London
METOFFICE_DATAPOINT_API_KEY=
METOFFICE_SITE_ID_LONDON=
METOFFICE_DATAHUB_API_KEY=
METOFFICE_DATAHUB_BASE_URL=https://gateway.api-management.metoffice.cloud
METOFFICE_DATAHUB_CONTEXT=/atmospheric-models/1.0.0
METOFFICE_DATAHUB_INTERVAL_SECONDS=1800
METOFFICE_SITE_SPECIFIC_API_KEY=
METOFFICE_SITE_SPECIFIC_BASE_URL=https://gateway.api-management.metoffice.cloud
METOFFICE_SITE_SPECIFIC_CONTEXT=/sitespecific/v0
METOFFICE_SITE_SPECIFIC_INTERVAL_SECONDS=1800

# Seoul
KMA_SERVICE_KEY=
KMA_NX_SEOUL=
KMA_NY_SEOUL=

# Forecast Guard V2
FORECAST_GUARD_ENABLED=true
FORECAST_GUARD_FAIL_SAFE=true
FORECAST_GUARD_RECALC_INTERVAL_SECONDS=1800
FORECAST_GUARD_RISK_SOURCE_THRESHOLD=2
FORECAST_GUARD_NEAR_DELTA_C=1.5
FORECAST_GUARD_NEW_HIGH_DELTA_C=0.5
FORECAST_GUARD_REBOUND_DELTA_3H_C=0.8
FORECAST_GUARD_PEAK_PASSED_MINUTES=30
FORECAST_GUARD_UNLOCK_NOAA_DROP_C=0.3
FORECAST_GUARD_UNLOCK_AUX_DROP_C=0.2
FORECAST_GUARD_UNLOCK_FUTURE_WARMING_C=0.2
```

---
*最后更新：2026-02-11 | 策略标准：V4.2.2+FGV2*
