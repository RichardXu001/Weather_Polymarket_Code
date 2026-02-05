# 🛠️ SKILL: 快速切换监控地点与数据源 (Location & Source Switching)

本手册详细介绍了在 `Weather Polymarket Edge` 系统中切换监控地点以及管理多个气象数据源的方法论、闭环流程及注意事项。

## 1. 核心流程 (Core Workflow)

### 第一步：定位 Polymarket 规则
1. **获取 Slug**：从 URL 中提取事件 Slug（如 `highest-temperature-in-london-on-february-5-2026`）。
2. **确认结算站点 (Crucial)**：
   - 展开事件页面的 "Market Context" 或 "Rules"。
   - 寻找 "Official reference station" 或 "Wunderground station URL"。
   - 提取其 **ICAO 代码**（如伦敦的 `EGLC`）。

### 第二步：获取地理坐标
1. **寻找 Lat/Lon**：在 Google 或气象站信息中搜索该代码的经纬度（如 `EGLC Lat Lon` -> `51.50, 0.05`）。
2. **目的**：模型源（Open-Meteo, Met.no）需要坐标来提供本地化预测。

### 第三步：参数化启动
```bash
./venv/bin/python3 weather_price_monitor.py --preset london
```

---

## 2. 数据源管理 (Data Source Management)

系统默认集成三个互补的数据源，以实现交叉验证：

| 数据源 | 类型 | 优势 | 局限 |
| :--- | :--- | :--- | :--- |
| **NOAA (METAR)** | 官方实测 | **结算真值**，直接来自机场气象站 | 仅提供实时观测，无预报 |
| **Open-Meteo** | 商业模型 | 响应速度快，带 1 小时逐小时预报 | 属于数值模拟，与实测有微小偏差 |
| **Met.no** | 全球顶级模型 | 挪威气象局出品，寒冷天气预报极其精准 | 采样点可能与机场有公里级偏差 |

### 如何切换/新增数据源？
脚本采用了模块化设计，若需新增数据源（如 AccuWeather）：
1. 在类中增加 `fetch_new_source` 方法。
2. 在 `run_once` 的 `sources` 字典中添加相应条目。
3. 系统会自动计算新增源的共识（Consensus）并计入分歧度（Divergence）检测。

---

## 3. 闭环验证 (Closing the Loop)

在切换地点或调整源后，必须验证：
1. **NOAA 一致性**：确认 `NO_ACTUAL` 是否正常返回。若 N/A，结算依据即丢失。
2. **预报趋势一致性**：检查 `OM_FORECAST` 和 `MN_FORECAST` 的方向是否一致。
3. **分歧度检查**：
   - `Divergence < 0.8`：数据可信度高。
   - `Divergence > 1.5`：可能存在某个源定位漂移，此时应调低交易杠杆。

---

## 4. 注意事项 (Precautions)
- **⚠️ 整数陷阱**：无论监测到多少位小数，Polymarket 结算最终只看 Wunderground 上的整数。
- **⚠️ 站点唯一性**：某些大城市有多个机场，务必核对 Rules 中的 ICAO 代码。
