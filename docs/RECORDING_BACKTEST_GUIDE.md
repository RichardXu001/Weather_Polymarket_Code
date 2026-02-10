# 🌦️ 天气数据工程手册 (V2.2)

本指南涵盖了 V4.2.2 套件中的全量工程化能力，包含数据录制、多源合并、仿真回测及交易审计。

---

## 📂 录制系统规范 (Data Logging)

### 1. 原始天气录制 (Weather Recordings)
- **目录**: `data/recordings/`
- **格式**: `weather_recording_{city}_{YYYYMMDD}_{HHMM}.csv`
- **采样**: 默认 30 秒/次。包含 NOAA, Open-Meteo, Met.no 的实测与预测值。
- **全量报价**: 自动平铺记录所有市场合约的 `Yes/No`, `Bid/Ask` 以及 `Vol`。

### 2. 交易信号存证 (Trade Audited Logs) [NEW]
针对策略触发的每一笔买入（或因价格保护跳过的信号），系统会生成独立的存证文件。
- **目录**: `data/trades/`
- **文件**: `trade_history_{city}.csv`
- **核心字段**:
    - `signal_type`: `BUY_DROP` (由下跌触发) / `BUY_FORCE` (由 17 点强买触发) / `SKIP_*` (被拦截信号)。
    - `execution_price`: 触发时的实时买一价 (Yes Ask)。
    - `reasoning`: 包含当时所有的决策依据（如 `Res:2/1`, `Dur:3`, `Depth:0.3`）。
    - `is_dry_run`: 标记是否为模拟模式。

---

## 🧩 回测引擎高级用法 (Backtest Engine)

### 1. 多 CSV 自动缝合 (Multi-CSV Fusion)
针对因重启导致的碎片化数据，支持通配符批量扫描与时序重连。
```bash
# 指令示例：一键合并并回测 London 全天数据
python3 backtest_engine.py "data/recordings/weather_recording_london_20260209_*.csv" --preset london
```
- **核心逻辑**: 基于 `timestamp` 自动排序、排重，确保“每日一单”的逻辑锁跨文件生效。

### 2. 结构化审计报告 (Automated Reporting)
回测完成后，系统将在 `backtest_reports/` 生成 Markdown 格式的报告。
| 本地时间 | 信号类型 | 交易合约 | 报价(USDC) | 详细判定原因 (Reasoning) |
| :--- | :--- | :--- | :--- | :--- |
| 16:15 | `BUY_DROP` | 30-31°F | 0.920 | V4.2.2 Phase3 Triggered (Res:1/1, Dur:0/1, Depth:0.3) |

---

## ⚡ 常用操作指令汇总

| 场景 | 命令示例 |
| :--- | :--- |
| **本地模拟运行** | `python3 weather_bot.py` (需设置 .env 中 DRY_RUN=true) |
| **单文件回测** | `python3 backtest_engine.py data/recordings/xxx.csv --preset nyc` |
| **跨城市通配回测** | `python3 backtest_engine.py "data/recordings/london_*.csv" --preset london` |

---

## 🛠️ 常见问题 (FAQ)

- **Q: 为什么记录里没有 NOAA 数据？**
    - **A**: 检查 ICAO 站代码是否正确，或官方源是否处于维护期（系统会自动切换至辅助源备份）。
- **Q: 为什么没触发买入？**
    - **A**: 查阅 `data/trades/` 或回测报告中的 `Reasoning` 字段。常见原因为共振数（Res）不足或未达到跌幅门槛（Depth）。

---
*最后更新：2026-02-10 | 策略版本：V4.2.2*
