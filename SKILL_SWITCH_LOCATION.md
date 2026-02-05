# 🛠️ SKILL: 快速切换监控地点与事件 (Location & Event Switching)

本手册详细介绍了在 `Weather Polymarket Edge` 系统中切换监控地点的方法论、闭环流程及注意事项。

## 1. 核心流程 (Core Workflow)

### 第一步：定位 Polymarket 规则
1. **获取 Slug**：从 URL 中提取事件 Slug（如 `highest-temperature-in-london-on-february-5-2026`）。
2. **确认结算站点 (Crucial)**：
   - 展开事件页面的 "Market Context" 或 "Rules"。
   - 寻找 "Official reference station" 或 "Wunderground station URL"。
   - 提取其 **ICAO 代码**（如伦敦市中心的 `EGLC`，伦敦西部的 `EGLL`）。

### 第二步：获取地理坐标
1. **寻找 Lat/Lon**：在 Google 或气象站信息中搜索该代码的经纬度。
   - 例如 `EGLC Lat Lon` -> `51.50, 0.05`。
2. **目的**：Open-Meteo 和 Met.no 需要精确坐标来提供模型预测。

### 第三步：参数化启动
利用重构后的 `weather_price_monitor.py` 支持的参数启动：

**方式 A：使用内置预设 (推荐)**
```bash
./venv/bin/python3 weather_price_monitor.py --preset london
```

**方式 B：手动指定参数**
```bash
./venv/bin/python3 weather_price_monitor.py --icao EGLC --slug [EVENT_SLUG] --lat 51.50 --lon 0.05
```

---

## 2. 闭环验证 (Closing the Loop)

在切换地点后，必须完成以下验证流程才能进入交易阶段：

1. **API 连接验证**：
   - 观察控制台是否出现 "N/A"。
   - 如果 NOAA (METAR) 是 N/A，说明 ICAO 代码可能错误。
2. **报价匹配验证**：
   - 检查仪表盘底部的选项（如 -3°C, -2°C）是否与网页端刷新保持一致。
   - 若价格全是 N/A，说明 Slug 错误或该事件已关闭。
3. **数据一致性检查**：
   - 检查 `Divergence`（分歧度）。
   - 若分歧度常年 > 5.0，可能意味着三个源中有一个定位到了错误的地点。

---

## 3. 注意事项 (Precautions)

- **⚠️ 结算站点漂移**：伦敦有两个常用机场（Heathrow 和 City Airport）。Polymarket 的不同事件可能使用不同站点，务必以 Rules 为准。
- **⚠️ 整数陷阱**：所有地点的结算均以 Wunderground 显示的**整数**为准。
- **⚠️ 时区差异**：预报 API 返回的是 UTC 时间。脚本已自动处理，但在研判“最高温度产生时间”时，请注意该地点的夏令时/标准时偏差。

---

## 4. 故障排除表

| 现象 | 可能原因 | 解决方法 |
| :--- | :--- | :--- |
| NOAA 显示 N/A | ICAO 代码无效 | 查阅 aviationweather.gov 确认代码 |
| 价格全是 N/A | Slug 错误或超时 | 检查 URL 段落，确认 API 访问权 |
| 预报曲线平直 | 坐标点位于海洋 | 微调 Lat/Lon 靠近陆地气象站 |
