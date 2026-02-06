# 🌦️ 天气数据录制与回测框架手册 (V1.0)

本指南旨在规范数据采集、存储以及回测验证流程。针对近期关于“目标温度”与“结果记录”的疑问，在此进行统一说明。

---

## 💡 核心概念澄清 (Core Concepts)

### 1. 什么是“目标温度” (Target Temp / Threshold)？
> **定义**：目标温度即 Polymarket 合约中的“结算阈值”（Strike Price）。
> **为什么每天不一样？**：
> - 每天的市场合约都在变（例如周一是“最高温是否 >= 8°C”，周二可能是“最高温是否 >= 7.5°C”）。
> - **首尔 vs 伦敦**：两地气候迥异，首尔目前处于零下，伦敦处于零上。
> - **作用**：策略内核利用该值来计算“非对称避让区”，即当气温跌破/接近该阈值时触发买入或等待。

### 4. 录制数据结构 (V2.0 - 全量原始数据)
> [!TIP]
> **设计哲学**: “原始采集 + 延迟计算”。所有未经处理的第三方 API 数据实时归档，回测脚本负责后续逻辑重构。

| 字段类别 | 字段名 | 说明 |
| :--- | :--- | :--- |
| **时间轴** | `timestamp` | 北京时间采样点 |
| | `local_time` | **站点本地时间 (HH:MM)** |
| | `local_hour` | 站点本地小时 (用于回测逻辑) |
| **原始天气** | `noaa_curr` | **NOAA (官方源)** 实时实测值 |
| | `om_curr / om_fore` | Open-Meteo **实测 / 1H 预测值** |
| | `mn_curr / mn_fore` | Met.no **实测 / 1H 预测值** |
| **市场报价** | `price_{选项}` | **平铺报价列**。如 `price_8°C` 代表该选项的 Ask。 |
| **审计信号** | `signal / reason` | 实盘当时的建议信号与触发/拒因。 |

---

## 📂 录制系统规范 (Data Logging)

### 1. 文件命名体系 (Naming Convention)
系统严格遵循“地点+时间”的双重标识命名法，确保离线分析时一眼识别数据源。

- **格式**: `weather_recording_{city}_{YYYYMMDD}_{HHMM}.csv`
- **示例**: `weather_recording_seoul_20260206_1630.csv`
- **触发机制**: 
    - **启动即生成**: 每次脚本启动时生成独立文件。
    - **切换即更新**: 当检测到日期跨天触发市场切换时，系统会**自动生成一个全新的 CSV 文件**，而不是在旧文件中追加。这确保了每个样本文件都对应一个明确的 Polymarket 交易日。

### 2. 采样频率
- **默认间隔**: **30 秒/次**。
- **设计初衷**: 30 秒能精确捕获 $V_{fit}$ 的导数（下跌斜率），而在分钟级成交的 Polymarket 环境下，此频率足以作为回测的基准频率。

## 📂 结果记录系统 (Outcome Logging)

系统在每日跨天时会自动结算前一日结果，并保存在独立文件中。

- **目录**: `data/outcomes/`
- **文件**: `outcome_{city}.csv`
- **字段**:
    - `date`: 交易日期
    - `target_threshold`: 当日合约的基准温度 (来自配置)
    - `actual_max`: 该地点在该交易日观察到的**全源最高实测温** (Consensus Max)
    - `result`: UNDER (实际 <= 目标) 或 OVER (实际 > 目标)，直接对应胜负结果。

---

## 📚 数据集目录 (Data Catalog)

| 城市 | 开始时间 | 天气特征 | 文件名 (参考) | 回测意义 |
| :--- | :--- | :--- | :--- | :--- |
| **首尔** | 2026-02-06 15:57 | 冷冻横盘 (-5.4~-5.5°C) | `weather_recording_seoul_20260206_1557.csv` | 验证下跌趋势识别的灵敏度 (IDLE 状态验证) |
| **伦敦** | 2026-02-05 15:14 | 稳步升温/降温 | `weather_edge_EGLC_20260205_1514.csv` | 传统 Peak Hour 交易信号验证 |

---

## ⚡ 回测操作指南 (Backtesting Guide)

所有数据录制后，可以使用 `backtest_engine.py` 进行参数化仿真，无需修改脚本代码。

### 1. 运行命令示例
```bash
# 基本运行 (自动识别预置)
python3 backtest_engine.py data/recordings/weather_recording_seoul_20260206_1557.csv --preset seoul

# 覆盖目标温度 (测试不同阈值下的表现)
python3 backtest_engine.py data/recordings/weather_recording_london_xxxx.csv --preset london --target 7.5
```

### 2. 核心参数说明
- `file`: (必选) 待分析的录制 CSV 文件。
- `--preset`: 选择预置城市，会自动加载 `locations.json` 中的时区偏移和默认 `target_temp`。
- `--target`: 强制覆盖目标温度。这对于测试“如果当时的门槛是 7.5 而不是 8.0，结果会如何”非常有用。

### 3. 注意事项
> [!CAUTION]
> **采样连续性**: 如果录制文件中途断开，`v_fit_history` 的连续性会受影响。回测引擎会自动重新热身，但信号可能延迟产生。

### 4. 回测引擎核心逻辑 (V2.0)
- **多合约扫描**: 自动识别 CSV 中所有 `price_X°C` 或 `X°C` 格式的合约列
- **精准匹配**: 只推荐 `target == predict_noaa(v_fit)` 的合约，即 `int(floor(v_fit + 0.5))` 与合约阈值完全匹配
- **业务逻辑复用**: 回测引擎 100% 调用 `StrategyKernel`，与实盘代码完全一致

---

## 🛠️ 故障排查 (Troubleshooting)

- **Q: 录制文件是空的或者只有表头？**
    - **A**: 检查 API 连通性（METAR/Open-Meteo），如果所有源均返回 `None`，系统将不会写入数据。
- **Q: 回测显示的信号与实盘当时看到的不同？**
    - **A**: 核对 `trading_hub.py` 与 `backtest_yesterday.py` 使用的 `StrategKernel` 版本是否一致。

---
*最后更新日期：2026-02-06*
