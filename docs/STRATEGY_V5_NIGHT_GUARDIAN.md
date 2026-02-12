# 策略 V5.1：Night Guardian（实现状态版）

## 1. 背景
事故场景（NYC，2026-02-10）暴露了 V4.2.2 的核心盲点：  
策略只看“当前是否回落”，没有对“今晚是否回暖冲高”做前置熔断，导致夜间改写当日最高温。

## 2. 当前已落地能力（代码已实现）

### 2.1 Forecast Guard V2（已上线）
- 开始时间：本地 12:00 后生效。
- 重算频率：默认每 30 分钟 (`FORECAST_GUARD_RECALC_INTERVAL_SECONDS=1800`)。
- 多源输入：`ecmwf_ifs` / `gfs_global` / `met.no` / `metoffice site-specific`（可用则参与）。
- 偏差校正：每个预报源先做 `bias = NOAA当前实测 - 当前预报`。
- 风险条件（任一命中即该源 risky）：
  - `NightPeak >= AfternoonPeak - 1.5°C`
  - `NightPeak >= DayMaxSoFar - 0.5°C`
  - `未来3小时升温 >= 0.8°C`
- 锁定动作：只要 `>=1` 源 risky，锁定并禁止 `BUY_DROP` + `BUY_FORCE`。

### 2.2 解锁机制（已上线）
必须同时满足：
1. 夜间风险峰值时间已过去 30 分钟；
2. NOAA 连续 3 次下降且累计降幅 >= 0.3°C；
3. OM/MN 至少一源连续 3 次下降且累计降幅 >= 0.2°C；
4. 至少 2 个预报源满足未来 2 小时最大升温 <= 0.2°C。

### 2.3 执行层联动（已上线）
- Guard 锁定时策略直接返回 `WAIT`，不再进入买入分支。
- 17:00 强制买入在锁定状态下自动失效（不再“无脑强买”）。

## 3. 与旧版 V5 设计的关系

旧文档中的 V5 设计项分为两类：

### 3.1 已实现
- Forecast Guard 前置拦截
- 夜间回暖风险熔断
- 强买约束

### 3.2 尚未实现（仍在规划）
- 风向 + 云量过滤（Warm Advection Filter）
- 独立 Phase 4 参数矩阵（当前仍沿用 V4.2.2 三阶段 + Guard 锁）
- 入场后动态对冲/退出（仓位级别）

## 4. 推荐运行参数（当前基线）
```env
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
*最后更新：2026-02-11 | 文档版本：V5.1（实现状态）*
