import asyncio
import logging
import re
import csv
import os
import requests
from datetime import datetime
from datetime import datetime as dt_datetime
from dotenv import load_dotenv
from weather_price_monitor import WeatherPriceMonitor
from engine.config import QuantConfig
from engine.data_feed import WeatherState
from engine.strategy import StrategyKernel
from engine.forecast_guard import ForecastGuardManager
from executor.poly_trader import PolyExecutor
from src.monitor.position_manager import PositionManager
from decimal import Decimal, ROUND_HALF_UP

# 加载环境变量
load_dotenv()

import time

# 全局启动时间，用于静默期判断
_STARTUP_TIME = time.time()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("WeatherBot")

_MONTH_NAME_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# 通知冷却缓存 (market, reason) -> last_send_time
_NOTIFICATION_COOLDOWN = {}

def send_dingtalk_notification(market, contract, price, shares, reason):
    """发送钉钉交易机会通知 (增加启动静默期与消息去重)"""
    now = time.time()
    if now - _STARTUP_TIME < 60:
        logger.info(f"[钉钉] 启动静默期，忽略通知: {market} {reason}")
        return

    # [NEW] 消息去重 logic: 6小时内相同的市场+理由只发一次 (除非是实际成交)
    # 成交通知 shares > 0 应当总是允许发送
    is_trade = shares > 0
    cache_key = (market, reason)
    if not is_trade:
        last_time = _NOTIFICATION_COOLDOWN.get(cache_key, 0)
        if now - last_time < 21600: # 6 hours
            logger.info(f"[钉钉] 消息处于冷却期，跳过重复通知: {market} {reason}")
            return
    
    webhook = os.getenv("DINGTALK_WEBHOOK")
    if not webhook:
        logger.warning("钉钉 Webhook 未配置，跳过通知")
        return
    
    total_cost = price * shares
    
    # 消息需要包含关键词 "beijixing" 或其他已设定的关键词
    message = f"""[Beijixing-WeatherBot] 🚨 Polymarket 交易触发提醒

📍 市场: {market}
🎯 目标合约: {contract}
💰 买入单价: {price:.3f} USDC
持有份额: {shares:.1f}
总计成本: {total_cost:.2f} USDC
📝 触发理由: {reason}
⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

请及时关注实盘动态！
-- [Robot: Weather Bot]"""
    
    payload = {
        "msgtype": "text",
        "text": {"content": message}
    }
    
    import json
    logger.info(f"[钉钉推送] Payload: {json.dumps(payload, ensure_ascii=False)}")
    
    try:
        resp = requests.post(webhook, json=payload, timeout=5)
        if resp.status_code == 200:
            logger.info(f"[钉钉] 通知发送成功")
            if not is_trade:
                _NOTIFICATION_COOLDOWN[cache_key] = now
        else:
            logger.warning(f"[钉钉] 通知发送失败: {resp.text}")
    except Exception as e:
        logger.error(f"[钉钉] 通知发送异常: {e}")


def send_fg_lock_dingtalk_notification(market, fg_reason, risk_count, available_sources, risky_sources):
    """发送 ForecastGuard 锁仓通知（仅在锁仓事件触发时调用）"""
    now = time.time()
    if now - _STARTUP_TIME < 60:
        logger.info(f"[钉钉] 启动静默期，忽略 FG 锁仓通知: {market}")
        return

    reason_text = fg_reason or "ForecastGuard locked"
    cache_key = (market, "FG_LOCK", reason_text)
    last_time = _NOTIFICATION_COOLDOWN.get(cache_key, 0)
    if now - last_time < 21600:  # 6 hours
        logger.info(f"[钉钉] FG 锁仓通知处于冷却期，跳过: {market} {reason_text}")
        return

    webhook = os.getenv("DINGTALK_WEBHOOK")
    if not webhook:
        logger.warning("钉钉 Webhook 未配置，跳过 FG 锁仓通知")
        return

    risky_text = ", ".join(risky_sources) if risky_sources else "N/A"
    message = f"""[Beijixing-WeatherBot] ⚠️ ForecastGuard 锁仓通知

📍 市场: {market}
🔒 状态: FG_LOCKED
📊 风险源: {risk_count}/{available_sources}
🧩 风险来源: {risky_text}
📝 原因: {reason_text}
⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

-- [Robot: Weather Bot]"""

    payload = {
        "msgtype": "text",
        "text": {"content": message}
    }

    import json
    logger.info(f"[FG钉钉推送] Payload: {json.dumps(payload, ensure_ascii=False)}")

    try:
        resp = requests.post(webhook, json=payload, timeout=5)
        if resp.status_code == 200:
            _NOTIFICATION_COOLDOWN[cache_key] = now
            logger.info(f"[钉钉] FG 锁仓通知发送成功")
        else:
            logger.warning(f"[钉钉] FG 锁仓通知发送失败: {resp.text}")
    except Exception as e:
        logger.error(f"[钉钉] FG 锁仓通知发送异常: {e}")


class WeatherBot:
    """并行多区域交易机器人"""
    
    def __init__(self):
        self.config = QuantConfig
        self.executor = PolyExecutor(self.config)
        self.pos_manager = PositionManager()
        self.forecast_guard = ForecastGuardManager(self.config)
        
    def _get_local_time_info(self, offset):
        """获取站点本地时间信息: (小时浮点数, HH:MM 字符串)"""
        import datetime as dt
        from datetime import timezone, timedelta
        utc_now = dt.datetime.now(timezone.utc)
        local_time = utc_now + timedelta(hours=offset)
        hour_float = local_time.hour + local_time.minute / 60.0
        time_str = local_time.strftime("%H:%M")
        return hour_float, time_str

    async def run_location_loop(self, preset_name, interval=60):
        """单个地点的监听与决策闭环 (支持跨天自动切换)"""
        from weather_price_monitor import PRESETS
        
        if preset_name not in PRESETS:
            logger.error(f"Preset '{preset_name}' not found!")
            return
            
        conf = PRESETS[preset_name]
        tz_offset = conf.get("tz_offset", 0)
        
        # 初始日期与录制文件名
        current_date_str = self._get_local_date(tz_offset)
        slug = self._get_dynamic_slug(conf['slug_template'], tz_offset)
        
        # 录制文件名格式: weather_recording_{city}_{YYYYMMDD}_{HHMM}.csv
        session_start = datetime.now().strftime('%Y%m%d_%H%M')
        current_recording_file = f"data/recordings/weather_recording_{preset_name}_{session_start}.csv"
        
        monitor = WeatherPriceMonitor(
            conf["icao"], slug, conf["lat"], conf["lon"]
        )
        # 为 monitor 指定 CSV 文件（虽然 hub 也会记录，但保持一致性）
        monitor.csv_file = current_recording_file
        
        # 每个地点维护独立的物理状态
        local_hour, local_time_str = self._get_local_time_info(tz_offset)
        state = WeatherState(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            local_time=local_time_str,
            local_hour=local_hour
        )
        
        logger.info(f"[+] Loop Started: {preset_name} | Slug: {slug} | TZ: {tz_offset}")

        # [NEW] 日志节流控制
        last_status_log_time = 0
        last_status_summary = ""
        
        unit = conf.get("unit", "C")
        
        # [NEW] 启动时尝试恢复当日历史最高温
        recovered_max = self._recover_today_max_temp(preset_name, current_date_str)
        if recovered_max is not None:
            state.max_temp_overall = recovered_max
            logger.info(f"[{preset_name:8}] 💾 成功从历史记录恢复当日最高温: {recovered_max:.2f}")

        # [NEW] 启动时尝试恢复当日交易状态 (防止重启后重复下单)
        if self._recover_today_trade_status(preset_name, current_date_str, tz_offset):
            state.has_traded_today = True
            logger.info(f"[{preset_name:8}] 🔒 成功从历史记录恢复已交易状态 (Has Traded Today)")

        # FG 锁仓状态机：仅在锁仓状态切换时发通知，避免循环内重复推送
        prev_fg_locked = False
        prev_fg_reason = ""
        
        while True:
            try:
                # 检查日期，如果跨天则刷新 slug
                now_date_str = self._get_local_date(tz_offset)
                if now_date_str != current_date_str:
                    # 跨天前将上一交易日 outcome 标记为最终结算
                    if state.max_temp_overall > -900:
                        self._upsert_outcome_row(
                            preset_name=preset_name,
                            date_str=current_date_str,
                            slug_id=slug,
                            noaa_max=state.max_temp_overall,
                            is_final=True,
                        )
                        logger.info(
                            f"[{preset_name:8}] 🧾 Outcome finalized for {current_date_str} | Max(NOAA): {state.max_temp_overall:.2f}"
                        )
                    logger.info(f"[{preset_name:8}] Date changed ({current_date_str} -> {now_date_str}), refreshing slug...")
                    current_date_str = now_date_str
                    slug = self._get_dynamic_slug(conf['slug_template'], tz_offset)
                    monitor.poly_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                    monitor.event_slug = slug
                    # [关键] 强制刷新 CSV 文件以对应新交易日
                    session_start = datetime.now().strftime('%Y%m%d_%H%M')
                    current_recording_file = f"data/recordings/weather_recording_{preset_name}_{session_start}.csv"
                    monitor.csv_file = current_recording_file
                    # 重置状态
                    state.has_traded_today = False
                    state.max_temp_overall = -999.0
                    state.v_fit_history = []
                    logger.info(f"[{preset_name:8}] New Session: {slug} | File: {current_recording_file}")

                    # [NEW] 跨天后再次检查当日交易状态 (防止跨天重启边缘case)
                    if self._recover_today_trade_status(preset_name, current_date_str, tz_offset):
                        state.has_traded_today = True
                        logger.info(f"[{preset_name:8}] 🔒 跨天检测到今日已有交易记录 (Has Traded Today)")

                # 1. 获取本地时间与数据 (注入差异化采样间隔)
                state.local_hour, state.local_time = self._get_local_time_info(tz_offset)
                loop = asyncio.get_event_loop()
                wd = await loop.run_in_executor(
                    None, 
                    lambda: monitor.fetch_all_sources(
                        om_interval=self.config.INTERVAL_OM, 
                        mn_interval=self.config.INTERVAL_MN
                    )
                )
                prices = await loop.run_in_executor(None, monitor.fetch_polymarket_asks)
                
                # 2. 状态录像
                state.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                state.noaa_curr = wd['sources']['NOAA (METAR)']['curr']
                state.om_curr = wd['sources']['Open-Meteo']['curr']
                state.om_fore = wd['sources']['Open-Meteo']['fore']
                state.mn_curr = wd['sources']['Met.no']['curr']
                state.mn_fore = wd['sources']['Met.no']['fore']
                state.consensus_curr = wd['avg_curr']
                state.consensus_fore = wd['avg_fore']
                
                if state.noaa_curr is not None:
                    prev_noaa_max = state.max_temp_overall
                    state.max_temp_overall = max(state.max_temp_overall, state.noaa_curr)
                    if state.max_temp_overall > (prev_noaa_max + 1e-9):
                        # 盘中实时账本：当天出现新高时 upsert 同一天记录
                        self._upsert_outcome_row(
                            preset_name=preset_name,
                            date_str=current_date_str,
                            slug_id=slug,
                            noaa_max=state.max_temp_overall,
                            is_final=False,
                        )
                if state.om_curr is not None:
                    state.max_temp_om = max(state.max_temp_om, state.om_curr)
                if state.mn_curr is not None:
                    state.max_temp_mn = max(state.max_temp_mn, state.mn_curr)
                
                # 维护连续下跌计数 (NOAA 核心基准)
                if state.noaa_curr is not None and state.max_temp_overall > -900:
                    if state.noaa_curr < state.max_temp_overall:
                        state.drop_count += 1
                    else:
                        state.drop_count = 0
                
                if state.om_curr is not None: state.om_history.append(state.om_curr)
                if state.mn_curr is not None: state.mn_history.append(state.mn_curr)
                if state.noaa_curr is not None: state.noaa_history.append(state.noaa_curr)
                
                v_fit = (state.om_curr * self.config.W1_OM + state.mn_curr * self.config.W2_MN) if (state.om_curr and state.mn_curr) else None
                if v_fit:
                    state.update_v_fit(v_fit)
                
                for hist in [state.noaa_history, state.om_history, state.mn_history, state.v_fit_history]:
                    if len(hist) > 10: hist.pop(0)

                # 3. 策略决策
                state.market_prices = prices # 存入全量报价

                guard_state = self.forecast_guard.assess(preset_name, state, conf)

                fg_locked_now = bool(guard_state.get("locked"))
                fg_reason_now = guard_state.get("reason", "")
                if fg_locked_now and (not prev_fg_locked or fg_reason_now != prev_fg_reason):
                    src_reports = guard_state.get("sources", {}) if isinstance(guard_state.get("sources"), dict) else {}
                    risky_sources = sorted(
                        [
                            name for name, rep in src_reports.items()
                            if isinstance(rep, dict) and rep.get("risky")
                        ]
                    )
                    send_fg_lock_dingtalk_notification(
                        market=preset_name.upper(),
                        fg_reason=fg_reason_now,
                        risk_count=int(guard_state.get("risk_count", 0)),
                        available_sources=int(guard_state.get("available_sources", 0)),
                        risky_sources=risky_sources
                    )
                prev_fg_locked = fg_locked_now
                prev_fg_reason = fg_reason_now
                
                # --- 新逻辑：NOAA 下跌触发 / 17点强买 ---
                signal, reason, target_temp = StrategyKernel.calculate_noaa_drop_signal(
                    state,
                    self.config,
                    state.max_temp_overall if state.max_temp_overall > -900 else None,
                    state.has_traded_today,
                    forecast_guard=guard_state,
                )
                
                # 兼容原有 v_fit 显示
                # 3.5 日志节流逻辑 (同一状态 60s 打印一次)
                guard_tag = "LOCKED" if guard_state.get("locked") else "PASS"
                status_summary = f"{guard_tag}({guard_state.get('risk_count', 0)}/{guard_state.get('available_sources', 0)}) | {signal:5}"
                now_ts = time.time()
                if status_summary != last_status_summary or (now_ts - last_status_log_time) > 60:
                    logger.info(
                        f"[{preset_name:8}] NOAA: {state.noaa_curr if state.noaa_curr else 0.0:.1f} | "
                        f"Max: {state.max_temp_overall if state.max_temp_overall > -900 else 0.0:.1f} | "
                        f"FG: {status_summary} | Reason: {reason}"
                    )
                    last_status_log_time = now_ts
                    last_status_summary = status_summary

                # 4. 执行决策 (Dry Run 或 Real)
                if signal in ['BUY_DROP', 'BUY_FORCE']:
                    # 确定合约：使用触发时的温度 (target_temp)
                    # [Unit Conversion] 如果单位是华氏度，进行转换
                    display_temp = target_temp
                    symbol = "°C"
                    if unit == "F" and target_temp is not None:
                        # NWS standard: Round Half Up (Asymmetric)
                        f_temp = target_temp * 1.8 + 32
                        display_temp = int(Decimal(str(f_temp)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                        symbol = "°F"
                    
                    target_contract_prefix = f"{int(display_temp)}{symbol}"
                    
                    # 智能搜索合约 (处理 NYC 的范围合约，如 "24-25°F")
                    target_contract = target_contract_prefix # 默认
                    contract_price = None
                    
                    if prices:
                        found = False
                        for title in prices.keys():
                            # 1. 前缀/包含匹配
                            if target_contract_prefix in title:
                                target_contract = title
                                found = True
                                break
                            
                            # 2. 华氏度范围匹配 (NYC 专用)
                            if unit == 'F':
                                # 匹配 "X-Y°F"
                                range_match = re.search(r'(\d+)-(\d+)°F', title)
                                if range_match:
                                    low, high = int(range_match.group(1)), int(range_match.group(2))
                                    if low <= display_temp <= high:
                                        target_contract = title
                                        found = True
                                        break
                                # 匹配 "X°F or below"
                                below_match = re.search(r'(\d+)°F or below', title)
                                if below_match and display_temp <= int(below_match.group(1)):
                                    target_contract = title
                                    found = True
                                    break
                                # 匹配 "X°F or higher"
                                higher_match = re.search(r'(\d+)°F or higher', title)
                                if higher_match and display_temp >= int(higher_match.group(1)):
                                    target_contract = title
                                    found = True
                                    break
                        
                        p_data = prices.get(target_contract)
                        if p_data:
                            contract_price = p_data.get('yes_ask') if isinstance(p_data, dict) else p_data
                    
                    # [Rule] 价格保护：dry run / real 统一要求 ask >= MIN_YES_ASK
                    should_execute = True
                    price_val = float(contract_price) if contract_price else 0.0
                    min_yes_ask = float(self.config.MIN_YES_ASK)
                    
                    if price_val + 1e-9 < min_yes_ask:
                        should_execute = False
                        reason_prefix = "Force buy" if signal == 'BUY_FORCE' else "Drop buy"
                        reason = f"{reason_prefix} skipped: Price {price_val:.3f} < MinAsk {min_yes_ask:.3f}"
                        logger.info(f"[{preset_name:8}] {reason} (Contract: {target_contract})")
                        
                        # 发送跳过通知
                        send_dingtalk_notification(
                            market=preset_name.upper(),
                            contract=target_contract,
                            price=price_val,
                            shares=0, # No shares bought
                            reason=reason
                        )
                        # 标记为已完成（避免重复尝试）
                        state.has_traded_today = True

                        # 5. 独立交易存证 (New Feature)
                        self._record_trade_event(
                            preset_name=preset_name,
                            city_name=conf.get("city_name", preset_name),
                            local_time=state.local_time,
                            signal=f"SKIP_{signal.split('_')[1]}",
                            slug=slug,
                            contract=target_contract,
                            price=price_val,
                            shares=0,
                            reason=reason
                        )
                            
                    if should_execute:
                        # 发送交易通知
                        send_dingtalk_notification(
                            market=preset_name.upper(),
                            contract=target_contract,
                            price=price_val,
                            shares=self.config.TRADE_SHARES,
                            reason=reason
                        )
                        
                        # 只有在非 Dry Run 且拿到价格时才下单
                        if not self.config.DRY_RUN and contract_price:
                            # 转换信号为标准 BUY 进行执行
                            await self.executor.execute_trade('BUY', monitor.event_slug, contract_price, self.config.TRADE_SHARES)
                        
                        # 标记今日已交易
                        state.has_traded_today = True
                        logger.info(f"[{preset_name:8}] ⚡ Trade Triggered ({signal}). Daily trade locked.")

                        # 记录交易事件并开始追踪生命周期
                        order_id = f"dry_{int(datetime.now().timestamp())}"
                        if not self.config.DRY_RUN:
                            # 实际下单时应从执行器获取真实 OrderID
                            # 这里假设执行器返回结果中包含 order_id
                            pass 

                        self.pos_manager.record_pending_order(
                            city_name=conf.get("city_name", preset_name),
                            local_time=state.local_time,
                            signal=signal,
                            slug=slug,
                            contract=target_contract,
                            price=price_val,
                            shares=self.config.TRADE_SHARES,
                            reason=reason,
                            order_id=order_id,
                            is_dry_run=self.config.DRY_RUN
                        )
                
                self._record_data(current_recording_file, state, prices, signal, reason, guard_state)

            except Exception as e:
                logger.error(f"[{preset_name}] Loop error: {e}")
                
            await asyncio.sleep(interval)

    def _get_local_date(self, offset):
        """获取站点本地日期字符串 (YYYY-MM-DD)"""
        import datetime as dt
        from datetime import timezone, timedelta
        utc_now = dt.datetime.now(timezone.utc)
        return (utc_now + timedelta(hours=offset)).strftime("%Y-%m-%d")

    def _recover_today_max_temp(self, preset_name, date_str):
        """恢复当日最高温：优先 outcome 账本，失败后回退扫描 recording"""
        outcome_max = self._recover_today_max_from_outcome(preset_name, date_str)
        if outcome_max is not None:
            return outcome_max

        import glob
        # 转换 2026-02-11 为 20260211
        search_date = date_str.replace("-", "")
        pattern = f"data/recordings/weather_recording_{preset_name}_{search_date}_*.csv"
        files = sorted(glob.glob(pattern))
        
        if not files:
            return None
            
        max_val = -999.0
        found = False
        
        # 遍历今日所有文件（防止重启多次产生多个文件）
        for fpath in files:
            try:
                with open(fpath, mode='r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        raw_val = row.get('noaa_curr')
                        if not raw_val or raw_val == 'N/A':
                            # 兼容旧字段名
                            raw_val = row.get('noaa_temp')
                        if raw_val and raw_val != 'N/A':
                            try:
                                val = float(raw_val)
                                if val > max_val:
                                    max_val = val
                                    found = True
                            except ValueError:
                                continue
            except Exception as e:
                logger.warning(f"[{preset_name:8}] 恢复历史文件失败 {fpath}: {e}")

        return max_val if found else None

    def _recover_today_max_from_outcome(self, preset_name, date_str):
        """从 outcome 账本恢复当日最高温 (is_final TRUE/FALSE 均可)"""
        filename = self._get_outcome_filename(preset_name)
        if not os.path.exists(filename):
            return None

        max_val = -999.0
        found = False
        try:
            with open(filename, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get('date', '')).strip() != date_str:
                        continue
                    val = self._safe_float(row.get('noaa_max'))
                    if val is None:
                        continue
                    if val > max_val:
                        max_val = val
                        found = True
        except Exception as e:
            logger.warning(f"[{preset_name:8}] 读取 outcome 恢复失败 {filename}: {e}")

        return max_val if found else None

    def _recover_today_trade_status(self, city_name, date_str, tz_offset):
        """检查今日交易记录文件，判断是否已完成交易 (防止重启后重复下单)"""
        # Dry run 不进行真实交易状态恢复，避免测试数据影响逻辑。
        if self.config.DRY_RUN:
            return False

        # 交易记录文件: data/trades/trade_history_{city}.csv
        filename = f"data/trades/trade_history_{city_name}.csv"
        if not os.path.exists(filename):
            return False
            
        target_day = None
        try:
            target_day = dt_datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return False

        try:
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get('is_dry_run', 'FALSE')).upper() == 'TRUE':
                        continue
                    try:
                        shares = float(row.get('shares', 0) or 0)
                    except ValueError:
                        shares = 0.0
                    if shares <= 0:
                        continue

                    status = str(row.get('status', '')).upper()
                    signal_type = str(row.get('signal_type', '')).upper()
                    is_trade_row = (status in {'PENDING', 'FILLED', 'WIN', 'LOSS', 'REDEEMED'}) or signal_type.startswith('BUY')
                    if not is_trade_row:
                        continue

                    slug = row.get('contract_slug', '')
                    if self._slug_matches_local_date(slug, target_day):
                        return True
        except Exception as e:
            logger.error(f"Error checking trade history for {city_name}: {e}")
            
        return False

    @staticmethod
    def _slug_matches_local_date(slug: str, target_day) -> bool:
        # slug 形如: highest-temperature-in-seoul-on-february-12-2026
        if not slug:
            return False
        m = re.search(r'on-([a-z]+)-(\d{1,2})-(\d{4})$', slug.lower())
        if not m:
            return False
        month_name, day_str, year_str = m.group(1), m.group(2), m.group(3)
        month = _MONTH_NAME_TO_NUM.get(month_name)
        if month is None:
            return False
        try:
            slug_day = dt_datetime(int(year_str), month, int(day_str)).date()
        except ValueError:
            return False
        return slug_day == target_day

    def _record_outcome(self, preset_name, date_str, slug_id, state):
        """结算并记录每日最终结果 (Outcome)"""
        if state.max_temp_overall <= -900:
            return
        self._upsert_outcome_row(
            preset_name=preset_name,
            date_str=date_str,
            slug_id=slug_id,
            noaa_max=state.max_temp_overall,
            is_final=True,
        )
        logger.info(f"[✓] Daily Summary Saved for {preset_name} | Max(NOAA): {state.max_temp_overall:.2f}")
        # 重置最高温以便第二天重新开始
        state.max_temp_overall = -999.0

    @staticmethod
    def _safe_float(raw_val):
        if raw_val is None:
            return None
        txt = str(raw_val).strip()
        if not txt or txt.upper() == "N/A":
            return None
        try:
            return float(txt)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_bool_str(raw_val) -> bool:
        if raw_val is None:
            return False
        return str(raw_val).strip().upper() in {"1", "TRUE", "YES", "Y"}

    @staticmethod
    def _format_noaa_max(noaa_max):
        if noaa_max is None:
            return ""
        return f"{float(noaa_max):.2f}"

    @staticmethod
    def _outcome_fieldnames():
        # 保持与历史文件兼容：旧列继续保留，新增 is_final
        return ["date", "slug_id", "target_threshold", "noaa_max", "result", "is_final"]

    def _get_outcome_filename(self, preset_name):
        data_dir = "data/outcomes"
        os.makedirs(data_dir, exist_ok=True)
        return f"{data_dir}/outcome_{preset_name}.csv"

    def _atomic_write_csv(self, filename, fieldnames, rows):
        tmp_filename = f"{filename}.tmp.{os.getpid()}"
        try:
            with open(tmp_filename, "w", newline="", encoding="utf-8") as fw:
                writer = csv.DictWriter(fw, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: row.get(k, "") for k in fieldnames})
            os.replace(tmp_filename, filename)
        finally:
            if os.path.exists(tmp_filename):
                try:
                    os.remove(tmp_filename)
                except OSError:
                    pass

    def _upsert_outcome_row(self, preset_name, date_str, slug_id, noaa_max, is_final=False, target_threshold="", result=""):
        """按 date 对 outcome 文件执行原子 upsert，避免同一天重复追加多行。"""
        filename = self._get_outcome_filename(preset_name)
        fieldnames = self._outcome_fieldnames()

        def _normalize_row(row):
            normalized = {k: str(row.get(k, "")).strip() for k in fieldnames}
            if self._parse_bool_str(normalized.get("is_final")):
                normalized["is_final"] = "TRUE"
            else:
                normalized["is_final"] = "FALSE"
            return normalized

        rows_by_date = {}
        ordered_dates = []
        if os.path.exists(filename):
            try:
                with open(filename, mode="r", encoding="utf-8") as fr:
                    reader = csv.DictReader(fr)
                    for raw_row in reader:
                        date_key = str(raw_row.get("date", "")).strip()
                        if not date_key:
                            continue
                        row = _normalize_row(raw_row)
                        prev = rows_by_date.get(date_key)
                        if prev is None:
                            rows_by_date[date_key] = row
                            ordered_dates.append(date_key)
                        else:
                            prev_max = self._safe_float(prev.get("noaa_max"))
                            new_max = self._safe_float(row.get("noaa_max"))
                            if new_max is not None and (prev_max is None or new_max > prev_max):
                                prev["noaa_max"] = self._format_noaa_max(new_max)
                            if row.get("slug_id"):
                                prev["slug_id"] = row["slug_id"]
                            if row.get("target_threshold"):
                                prev["target_threshold"] = row["target_threshold"]
                            if row.get("result"):
                                prev["result"] = row["result"]
                            prev["is_final"] = "TRUE" if (
                                self._parse_bool_str(prev.get("is_final")) or self._parse_bool_str(row.get("is_final"))
                            ) else "FALSE"
            except Exception as e:
                logger.warning(f"[{preset_name:8}] 读取 outcome 文件失败，改为重建 {filename}: {e}")
                rows_by_date = {}
                ordered_dates = []

        incoming = {
            "date": date_str,
            "slug_id": slug_id or "",
            "target_threshold": target_threshold or "",
            "noaa_max": self._format_noaa_max(noaa_max if (noaa_max is not None and noaa_max > -900) else None),
            "result": result or "",
            "is_final": "TRUE" if is_final else "FALSE",
        }
        prev = rows_by_date.get(date_str)
        if prev is None:
            rows_by_date[date_str] = incoming
            ordered_dates.append(date_str)
        else:
            prev_max = self._safe_float(prev.get("noaa_max"))
            incoming_max = self._safe_float(incoming.get("noaa_max"))
            if incoming_max is not None and (prev_max is None or incoming_max > prev_max):
                prev["noaa_max"] = self._format_noaa_max(incoming_max)
            if incoming.get("slug_id"):
                prev["slug_id"] = incoming["slug_id"]
            if incoming.get("target_threshold"):
                prev["target_threshold"] = incoming["target_threshold"]
            if incoming.get("result"):
                prev["result"] = incoming["result"]
            prev["is_final"] = "TRUE" if (
                self._parse_bool_str(prev.get("is_final")) or self._parse_bool_str(incoming.get("is_final"))
            ) else "FALSE"

        ordered_rows = [rows_by_date[d] for d in ordered_dates if d in rows_by_date]
        self._atomic_write_csv(filename, fieldnames, ordered_rows)

    def _get_dynamic_slug(self, template, offset):
        """根据站点本地时间动态生成 Slug"""
        import datetime as dt
        from datetime import timezone, timedelta
        
        utc_now = dt.datetime.now(timezone.utc)
        local_time = utc_now + timedelta(hours=offset)
        
        # Polymarket 格式: month-day-year (lowercase, e.g. february-5-2026)
        month_name = local_time.strftime("%B").lower()
        day = local_time.day
        year = local_time.year
        
        return template.format(month=month_name, day=day, year=year)

        return template.format(month=month_name, day=day, year=year)

    def _record_trade_event(self, preset_name, city_name, local_time, signal, slug, contract, price, shares, reason):
        """记录具体的交易触发信号到独立文件 (data/trades/)"""
        data_dir = "data/trades"
        os.makedirs(data_dir, exist_ok=True)
        # 与持仓生命周期文件解耦，避免不同 schema 污染 trade_history。
        filename = f"{data_dir}/trade_events_{city_name}.csv"
        
        file_exists = os.path.isfile(filename)
        row = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'local_time': local_time,
            'signal_type': signal,
            'contract_slug': slug,
            'target_asset': contract,
            'execution_price': f"{price:.3f}" if price else "0.000",
            'shares': shares,
            'reasoning': reason,
            'is_dry_run': "TRUE" if self.config.DRY_RUN else "FALSE"
        }
        
        try:
            with open(filename, 'a', newline='', encoding='utf-8') as f:
                fieldnames = ['timestamp', 'local_time', 'signal_type', 'contract_slug', 'target_asset', 'execution_price', 'shares', 'reasoning', 'is_dry_run']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
            logger.info(f"[{preset_name:8}] 📊 Trade event logged to {filename}")
        except Exception as e:
            logger.error(f"[{preset_name:8}] Failed to write trade log: {e}")

    def _record_data(self, filename, state, prices, signal, reason, guard_state=None):
        """记录实时数据到 CSV (全量原始记录，平铺报价列)"""
        if not prices:
            logger.warning(f"[DEBUG] No prices fetched for {filename}")
            
        data_dir = os.path.dirname(filename)
        os.makedirs(data_dir, exist_ok=True)
        
        file_exists = os.path.isfile(filename)
        
        # 基础字段 (仅保留原始输入)
        row = {
            'timestamp': state.timestamp,
            'local_time': state.local_time,
            'local_hour': f"{state.local_hour:.2f}",
            'noaa_curr': state.noaa_curr,
            'om_curr': state.om_curr,
            'om_fore': state.om_fore,
            'mn_curr': state.mn_curr,
            'mn_fore': state.mn_fore,
            'signal': signal,
            'reason': reason,
            'fg_locked': guard_state.get('locked') if guard_state else None,
            'fg_risk_count': guard_state.get('risk_count') if guard_state else None,
            'fg_available_sources': guard_state.get('available_sources') if guard_state else None,
            'fg_reason': guard_state.get('reason') if guard_state else None,
            'fg_afternoon_peak': guard_state.get('avg_afternoon_peak') if guard_state else None,
            'fg_night_peak': guard_state.get('avg_night_peak') if guard_state else None,
            'fg_night_peak_time': guard_state.get('latest_risky_peak_utc').strftime("%H:%M") if (guard_state and guard_state.get('latest_risky_peak_utc')) else None,
            'fg_max_bias': guard_state.get('max_bias') if guard_state else None,
            'fg_max_2h_warming': guard_state.get('max_2h_warming') if guard_state else None
        }
        
        # 记录 Yes, No 的 Ask/Bid 和 Volume
        price_cols = []
        if prices:
            for title, p_data in prices.items():
                if isinstance(p_data, dict):
                    row[f"{title}_yes_ask"] = p_data.get('yes_ask')
                    row[f"{title}_yes_bid"] = p_data.get('yes_bid')
                    row[f"{title}_no_ask"] = p_data.get('no_ask')
                    row[f"{title}_no_bid"] = p_data.get('no_bid')
                    row[f"{title}_vol"] = p_data.get('vol')
                    price_cols.extend([
                        f"{title}_yes_ask", f"{title}_yes_bid", 
                        f"{title}_no_ask", f"{title}_no_bid", 
                        f"{title}_vol"
                    ])

        with open(filename, 'a', newline='') as f:
            base_fields = [
                'timestamp', 'local_time', 'local_hour', 
                'noaa_curr', 'om_curr', 'om_fore', 'mn_curr', 'mn_fore',
                'signal', 'reason',
                'fg_locked', 'fg_risk_count', 'fg_available_sources', 'fg_reason',
                'fg_afternoon_peak', 'fg_night_peak', 'fg_night_peak_time', 'fg_max_bias', 'fg_max_2h_warming'
            ]
            
            # 如果是新文件，只有在拿到报价后才创建并写入 header
            if not file_exists:
                if not prices:
                    logger.warning(f"[{filename}] Skipping first log: No price data to initialize headers.")
                    return
                fieldnames = base_fields + sorted(price_cols)
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            else:
                # 读取第一行获取 header
                with open(filename, 'r') as fr:
                    reader = csv.reader(fr)
                    try:
                        existing_headers = next(reader)
                    except StopIteration:
                        # 文件存在但为空的情况
                        if not prices: return
                        existing_headers = base_fields + sorted(price_cols)
                        # 这里需要重新打开文件写入 header，或者在此处处理
                        pass 
                
                # 兼容处理：如果现有 header 没有价格列，但在新行中有，
                # 对于已经创建的文件，我们只能忽略或者在此处补充（比较复杂）。
                # 既然我们会删除旧文件重启，确保第一次有数据即可。
                writer = csv.DictWriter(f, fieldnames=existing_headers, extrasaction='ignore')
            
            writer.writerow(row)

    async def run_parallel(self, presets, interval=30):
        """并行运行多个 Preset"""
        logger.info(f"[*] Launching Multi-Location Engine: {presets}")
        logger.info(f"[*] Mode: {'DRY RUN' if self.config.DRY_RUN else 'REAL'}")
        
        # 启动后台持仓监控与报告任务
        asyncio.create_task(self.monitor_and_report_loop(presets))
        
        tasks = [self.run_location_loop(p, interval) for p in presets]
        await asyncio.gather(*tasks)

    async def monitor_and_report_loop(self, presets, report_interval_hours=4):
        """每隔 4 小时更新一次状态并发送钉钉汇总报告 (启动时立即推送一次)"""
        logger.info(f"[*] Postion Monitor Loop Started (Interval: {report_interval_hours}h)")
        
        while True:
            try:
                # 1. 更新所有地点的持仓状态
                logger.info("[监控] 正在更新所有地点的持仓状态并准备报告...")
                for p in presets:
                    self.pos_manager.update_positions_status(p)
                
                # 2. 生成汇总报告并发送
                report_text = self.pos_manager.get_summary_report()
                
                webhook = os.getenv("DINGTALK_WEBHOOK")
                if webhook:
                    payload = {
                        "msgtype": "text",
                        "text": {
                            "content": f"[Beijixing-WeatherBot] 📊 定期持仓汇总报告\n\n当前持仓状态\n{report_text}\n\n-- [Robot: Weather Bot]"
                        }
                    }
                    import json
                    logger.info(f"[监控推送] Payload: {json.dumps(payload, ensure_ascii=False)}")
                    # 使用 run_in_executor 避免同步请求阻塞异步循环
                    def _send():
                        try:
                            # 增加对响应的深度校验
                            r = requests.post(webhook, json=payload, timeout=15)
                            logger.info(f"[监控] 钉钉响应: {r.status_code} - {r.text}")
                        except Exception as e:
                            logger.error(f"[监控] 发送请求异常: {e}")

                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, _send)
                else:
                    logger.warning("[监控] 钉钉 Webhook 未配置，无法发送汇总报告")

            except Exception as e:
                logger.error(f"[监控] 报告循环异常: {e}")
            
            # 最后等待间隔时间
            await asyncio.sleep(report_interval_hours * 3600)


if __name__ == "__main__":
    import argparse
    from weather_price_monitor import PRESETS

    parser = argparse.ArgumentParser(description="Polymarket 天气自动交易机器人")
    parser.add_argument(
        "--presets", 
        nargs="+", 
        help="要运行的地点预设 (若不指定则读取 .env 中的 ACTIVE_LOCATIONS)"
    )
    parser.add_argument(
        "--interval", 
        type=int, 
        default=30, 
        help="采样主循环间隔(秒)，默认 30s"
    )
    
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dry-run', action='store_true', help='强制开启 Dry Run 模拟模式 (覆盖 .env)')
    group.add_argument('--real', action='store_true', help='强制开启实盘模式 (覆盖 .env)')
    
    args = parser.parse_args()
    
    # 模式开关：优先级 CLI > .env
    if args.dry_run:
        QuantConfig.DRY_RUN = True
        logger.info("[CLI] 模式强制切换为: DRY RUN")
    elif args.real:
        QuantConfig.DRY_RUN = False
        logger.info("[CLI] 模式强制切换为: REAL (请注意风险!)")
    
    # 获取运行城市：优先级 CLI > .env > Default
    active_cities = args.presets or QuantConfig.ACTIVE_LOCATIONS
    
    # 过滤掉不存在的城市
    valid_cities = [c for c in active_cities if c in PRESETS]
    if not valid_cities:
        logger.error(f"没有可运行的有效地点! 输入: {active_cities}")
        exit(1)
        
    logger.info(f"[*] 准备启动地点: {valid_cities}")
    
    bot = WeatherBot()
    try:
        asyncio.run(bot.run_parallel(valid_cities, interval=args.interval))
    except KeyboardInterrupt:
        logger.info("[!] 收到退出信号，正在停止服务...")
