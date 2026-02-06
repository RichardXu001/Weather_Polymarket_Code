# 🌦️ Weather & Polymarket Edge 监控系统

本系统用于实时监控首尔机场气温与 Polymarket 预测市场价格，通过多源数据校验（NOAA, Open-Meteo, Met.no）和 1 小时趋势预报辅助交易决策。

## 1. 快速启动 (生产环境)
核心入口为 `trading_hub.py`，支持多区域并行监控与自动交易：
```bash
# 1. 默认启动 (伦敦 + 首尔 并行)
./venv/bin/python3 trading_hub.py

# 2. 仅启动伦敦 (London)
./venv/bin/python3 trading_hub.py --presets london

# 3. 指定地点与高频采样 (15秒一次)
./venv/bin/python3 trading_hub.py --presets london seoul --interval 15
```

## 2. 命令行参数详解
| 参数 | 说明 | 示例 |
| :--- | :--- | :--- |
| `--presets` | 运行地点预设，支持 `london`, `seoul` | `--presets london` |
| `--interval` | 采样频率(秒)，默认 30s | `--interval 60` |

---

## 3. 辅助监控工具 (单项调试)
如果只想快速查看某一地点的可视化仪表盘而不参与交易：
```bash
./venv/bin/python3 weather_price_monitor.py --preset london
```

## 2. 核心功能说明

### 2.1 多源气象监控 (3D Sync)
集成三个权威数据源以降低单一源风险：
- **NOAA (METAR)**：官方实测真值（结算核心参考）。
- **Open-Meteo / Met.no**：提供实时值与 **+1 小时趋势预报**。
- **共识逻辑**：自动计算三方平均值，并在分歧过大（> 0.8°C）时发出警告。

### 2.2 决策看板
实时采集 Polymarket 对应事件的所有选项价格，并以“信心进度条”形式展示，直观反映市场热度。

### 2.3 自动日志 (CSV)
每次运行会自动生成 `weather_edge_[ICAO]_[时间].csv`，记录所有原始数据。

## 3. 版本管理 (Git)
项目已初始化 Git 仓库。每次修改逻辑后，建议执行：
```bash
git add .
git commit -m "描述您的修改"
```

## 3. CSV 字段含义
| 字段 | 含义 |
| :--- | :--- |
| `NO_ACTUAL` | **机场官方实测气温**（结算依据） |
| `consensus_actual` | 三个来源的当前共识平均温 |
| `consensus_forecast` | 预测的 1 小时后气温 |
| `divergence` | 数据源分歧度（越小越可靠） |
| `OM_... / MN_...` | Open-Meteo 与 Met.no 的明细读数 |

## 4. 特别提示
- **结算规则**：Polymarket 通常以整数结算，若气温在 -4.5°C 这种边界点波动，风险极高。
- **冷空气路径**：深冬时节最高温未必出现在下午，请结合预报趋势图进行决策。
