# 策略 V5.2：Night Guardian（实现状态版）

## 1. 背景
事故场景（NYC，2026-02-10）暴露了 V4.2.2 的核心盲点：  
策略只看“当前是否回落”，没有对“今晚是否回暖冲高”做前置熔断，导致夜间改写当日最高温。

## 2. 当前已落地能力（代码已实现）

### 2.1 Forecast Guard V2（已上线）
- 开始时间：本地 12:00 后生效。
- 重算频率：默认每 30 分钟 (`FORECAST_GUARD_RECALC_INTERVAL_SECONDS=1800`)。
- 多源输入：`ecmwf_ifs` / `gfs_global` / `met.no` / `metoffice site-specific`（可用则参与）。
- 偏差校正：每个预报源先做 `bias = NOAA当前实测 - 当前预报`。
- 风险条件（按源独立判断）：
  - 在 17:00 后识别“夜间风险区间窗口”（风险线以上的连续区间 + 持续点数 + 显著性）。
  - 报告从“单点峰值”升级为“区间 + 代表时刻”：
    - 区间：`start_hour ~ end_hour`（连续满足风险线的窗口）
    - 代表时刻：`max@time` 取“窗口内最高温的最后一次出现时间”（避免 plateau/多峰时锚定过早，如 17:00 其实最高温持续到 22:00）。
  - 单源命中后记为 `risky`，并生成 `risk_desc`（如 `夜间风险区间[...]`）。
- 锁定动作：
  - `risky` 源数量达到阈值即锁定（`FORECAST_GUARD_RISK_SOURCE_THRESHOLD`，系统内最低按 2 源生效）。
  - 锁定后禁止 `BUY_DROP` 与 `BUY_FORCE`。

### 2.2 Fail-safe 与锁仓原因（已上线）
FG 的 `Reason` 目前主要来自以下几类：
1. `Risky forecast sources N [...]`  
   - 说明：多源夜间回暖风险达到阈值，进入风险锁。
2. `No NOAA anchor`  
   - 说明：NOAA 实测锚点缺失（常见于 NOAA 超时/SSL 抖动），在 `FORECAST_GUARD_FAIL_SAFE=true` 下需连续缺失 N 次才触发锁（避免一次性抖动立刻锁仓）。
3. `No forecast sources available`  
   - 说明：预报源全部不可用，在 fail-safe 下直接锁。
4. `Guard fetch failed: ...`  
   - 说明：FG 计算异常（抓取/解析）进入兜底报告，在 fail-safe 下可触发锁。

### 2.3 解锁机制（已上线）
必须同时满足：
1. 夜间风险“窗口结束时间”已过去 30 分钟（使用 `night_peak_anchor_time` 作为解锁锚点，避免 plateau/多峰提前解锁）；
2. NOAA 连续 3 次下降且累计降幅 >= 0.3°C；
3. OM/MN 至少一源连续 3 次下降且累计降幅 >= 0.2°C；
4. 至少 2 个预报源满足未来 2 小时最大升温 <= 0.2°C。

### 2.4 告警去抖（已上线）
- 参数：`FORECAST_GUARD_NOAA_ANCHOR_ALERT_STREAK`（默认 `2`）。
- 作用范围：仅影响 `No NOAA anchor` 的钉钉锁仓通知。
- 规则：`No NOAA anchor` 需连续出现 N 次才发锁仓通知；策略锁仓本身仍即时生效。

### 2.5 执行层联动（已上线）
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
FORECAST_GUARD_NOAA_ANCHOR_LOCK_STREAK=3
FORECAST_GUARD_NOAA_ANCHOR_ALERT_STREAK=2
FORECAST_GUARD_PEAK_THRESHOLD_C=1.5
FORECAST_GUARD_PEAK_MIN_POINTS=2
FORECAST_GUARD_PEAK_PROMINENCE_C=0.3
FORECAST_GUARD_PEAK_PASSED_MINUTES=30
FORECAST_GUARD_UNLOCK_NOAA_DROP_C=0.3
FORECAST_GUARD_UNLOCK_AUX_DROP_C=0.2
FORECAST_GUARD_UNLOCK_FUTURE_WARMING_C=0.2
```

---
*最后更新：2026-02-12 | 文档版本：V5.2（实现状态）*
