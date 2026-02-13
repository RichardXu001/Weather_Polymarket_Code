# 天气量化交易系统：架构设计文档 (v1.3)

## 1. 系统概览
当前实盘策略为 **V4.2.2 + Forecast Guard V2 (FGV2)**。  
核心目标：在 13:00 后的交易窗口内识别“见顶回落”机会，同时通过预测风控拦截“夜间回暖改写日高温”的失效场景。

## 2. 核心架构组件

### 2.1 数据采集层 (Data Ingestion)
- 实测层：AviationWeather METAR（NOAA）作为温度锚点。
- 预报层：Open-Meteo (`ecmwf_ifs`, `gfs_global`) + Met.no + Met Office site-specific（可用时）。
- 市场层：Polymarket 盘口与报价深度。
- 站点配置：通过 `locations.json` 读取经纬度、时区、温标（C/F）。

### 2.2 风控层 (Forecast Guard V2)
- 周期：从本地 12:00 开始，默认每 1800 秒（30 分钟）重算。
- 偏差修正：按源计算 `bias = NOAA当前实测 - 该源当前预报`，用校正后温度参与风险判断。
- 风险锁：识别 17:00 后“夜间风险峰值”（接近日间参考高点 + 局部峰值 + 持续点数 + 显著性）。
- 触发规则：
  - `Risky forecast sources >= FORECAST_GUARD_RISK_SOURCE_THRESHOLD`（系统内最低按 2 源生效）即锁定。
  - 或 `available_sources == 0` 且 `FORECAST_GUARD_FAIL_SAFE=true` 时锁定。
- 解锁规则：峰值时间过去 + 多源实测确认降温 + 未来2小时不再明显回暖。
- 细节文档：`docs/STRATEGY_V5_NIGHT_GUARDIAN.md`（完整锁仓原因、告警去抖与参数说明）。

### 2.3 策略判定层 (Strategy Kernel)
- 三阶段下跌触发（Phase1/2/3）。
- 多源共振 + 每源峰值锚定（NOAA/OM/MN）。
- FGV2 在信号前做硬拦截：若锁定直接 `WAIT`。

### 2.4 执行与保护层 (Execution & Safety)
- 下单前价格保护（可配置）：dry run / 实盘统一 `yes_ask < 0.9` 跳过（默认）。
- Dry Run / Real 模式统一入口。
- NYC 华氏温标与范围合约智能匹配（如 `30-31°F`）。

### 2.5 审计与回测层 (Audit & Backtest)
- 原始录制：`data/recordings/`
- 交易存证：`data/trades/`
- 回测报告：`backtest_reports/`
- 12h 预测抓取：`scripts/fetch_12h_forecasts.py`

## 3. 关键代码映射
```text
engine/
├── config.py             # 全局参数（含 FGV2 配置）
├── data_feed.py          # WeatherState
├── strategy.py           # V4.2.2 + FGV2 前置拦截
└── forecast_guard.py     # Forecast Guard V2 风险锁/解锁核心

weather_bot.py            # 主循环（采集 -> Guard -> Strategy -> 执行）
weather_price_monitor.py  # 多源天气 + Polymarket 抓取
backtest_engine.py        # 回测与报告
scripts/fetch_12h_forecasts.py
```

## 4. 运行时配置（核心）
- 策略触发：`STRATEGY_P1_* / P2_* / P3_* / STRATEGY_FORCE_BUY_TIME`
- 风控开关：`FORECAST_GUARD_ENABLED`, `FORECAST_GUARD_FAIL_SAFE`
- 风控告警去抖：`FORECAST_GUARD_NOAA_ANCHOR_ALERT_STREAK`（仅影响 `No NOAA anchor` 告警，默认 2 次）
- 风控阈值：`FORECAST_GUARD_NEAR_DELTA_C`, `FORECAST_GUARD_NEW_HIGH_DELTA_C`, `FORECAST_GUARD_REBOUND_DELTA_3H_C`
- 风控解锁：`FORECAST_GUARD_PEAK_PASSED_MINUTES`, `FORECAST_GUARD_UNLOCK_*`
- Met Office site-specific：`METOFFICE_SITE_SPECIFIC_*`

## 5. 当前实现边界
- 已实现：FGV2 风险锁/解锁、30 分钟重算、多源偏差修正、强买阻断。
- 待扩展：风向/云量过滤、基于仓位的动态对冲与退出策略。

---
*最后更新：2026-02-12 | 文档版本：v1.4*
