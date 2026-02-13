# Polymarket Weather Bot (V4.2.2+FGV2)

自动化天气交易机器人，面向 Polymarket `Highest Temperature` 市场。  
当前实盘逻辑为 `V4.2.2 + Forecast Guard V2`：保留三阶段下跌触发，同时用预测风控拦截夜间回暖风险。

## 1. 快速启动

### 1.1 运行模式
- `DRY_RUN=true`：模拟模式（默认推荐）
- `DRY_RUN=false`：实盘模式

### 1.2 启动命令与参数
基本运行（默认使用 `.env` 中的 `DRY_RUN` 配置）：
```bash
./venv/bin/python3 weather_bot.py
```

**命令行覆盖模式（推荐）**：  
使用以下参数可以强制指定运行模式，优先级高于 `.env`：
- `./venv/bin/python3 weather_bot.py --dry-run` : 强制模拟模式
- `./venv/bin/python3 weather_bot.py --real`    : 强制实盘模式（有风险提示）

**其他常用参数**：
- `./venv/bin/python3 weather_bot.py --presets seoul nyc` : 仅运行特定地点
- `./venv/bin/python3 weather_bot.py --interval 10`       : 修改采样间隔（秒）

## 2. 当前策略要点

### 2.1 交易触发（V4.2.2）
- 三阶段时窗：Phase1/Phase2/Phase3（本地时间）
- 多源共振：NOAA + OM + MN
- 17:00 `BUY_FORCE`（但会被 Forecast Guard 风控锁拦截）
- 价格保护（可配置）：`yes_ask < 0.9` 跳过（dry run / 实盘统一，默认）

### 2.2 Forecast Guard V2（FGV2）
- 从本地 12:00 开始，每 30 分钟重算风险
- 预报先做 bias 校正：`bias = NOAA当前 - 预报当前`
- 风险源判定：识别“夜间风险峰值”（17:00后接近日间参考高点、满足持续点数与显著性）
- 锁仓触发（禁止 `BUY_DROP`/`BUY_FORCE`）：
  - `Risky forecast sources >= FORECAST_GUARD_RISK_SOURCE_THRESHOLD`（系统内最低按 2 源生效）
  - 或 `available_sources == 0` 且 `FORECAST_GUARD_FAIL_SAFE=true`
- 解锁要求：峰值过去 + 多源实测连续降温 + 未来2小时不再明显回暖

详细说明见：
- `docs/STRATEGY_V5_NIGHT_GUARDIAN.md`（锁仓原因、解锁与告警去抖细则）
- `docs/STRATEGY_DYNAMIC_DROP.md`
- `docs/ARCHITECTURE_GUIDE.md`

## 3. 数据源

### 3.1 全球实测
- AviationWeather METAR（NOAA）

### 3.2 全球预报
- Open-Meteo `ecmwf_ifs`
- Open-Meteo `gfs_global`
- MET Norway `locationforecast`
- Met Office DataHub `site-specific`（已验证可用于 4 地）

## 4. 关键配置（.env）

### 4.1 基础
```env
DRY_RUN=true
STRATEGY_MIN_YES_ASK=0.9
ENABLE_LONDON=true
ENABLE_NEW_YORK=true
ENABLE_SEOUL=true
ENABLE_ANKARA=true
```

### 4.2 Forecast Guard V2
```env
FORECAST_GUARD_ENABLED=true
FORECAST_GUARD_FAIL_SAFE=true
FORECAST_GUARD_RECALC_INTERVAL_SECONDS=1800
FORECAST_GUARD_RISK_SOURCE_THRESHOLD=2
FORECAST_GUARD_NOAA_ANCHOR_ALERT_STREAK=2
FORECAST_GUARD_NEAR_DELTA_C=1.5
FORECAST_GUARD_NEW_HIGH_DELTA_C=0.5
FORECAST_GUARD_REBOUND_DELTA_3H_C=0.8
FORECAST_GUARD_PEAK_PASSED_MINUTES=30
FORECAST_GUARD_UNLOCK_NOAA_DROP_C=0.3
FORECAST_GUARD_UNLOCK_AUX_DROP_C=0.2
FORECAST_GUARD_UNLOCK_FUTURE_WARMING_C=0.2
FORECAST_GUARD_PEAK_THRESHOLD_C=1.5
FORECAST_GUARD_PEAK_MIN_POINTS=2
FORECAST_GUARD_PEAK_PROMINENCE_C=0.3
```

### 4.3 Met Office site-specific
```env
METOFFICE_SITE_SPECIFIC_API_KEY=
METOFFICE_SITE_SPECIFIC_BASE_URL=https://gateway.api-management.metoffice.cloud
METOFFICE_SITE_SPECIFIC_CONTEXT=/sitespecific/v0
METOFFICE_SITE_SPECIFIC_INTERVAL_SECONDS=1800
```

## 5. 常用脚本

- 12 小时多源预报抓取：
```bash
python3 scripts/fetch_12h_forecasts.py
```

- 全球源可用性探测：
```bash
python3 scripts/probe_global_forecast_sources.py
```

## 6. 目录

- `weather_bot.py`：实盘主循环
- `engine/strategy.py`：V4.2.2 触发内核
- `engine/forecast_guard.py`：FGV2 风控锁/解锁
- `engine/config.py`：参数配置
- `data/recordings/`：实时录制
- `data/trades/`：交易存证
- `docs/`：策略与架构文档

---
免责声明：仅供研究，实盘盈亏自负。
