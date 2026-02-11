import asyncio
import logging
import re
import csv
import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from weather_price_monitor import WeatherPriceMonitor
from engine.config import QuantConfig
from engine.data_feed import WeatherState
from engine.strategy import StrategyKernel
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

def send_dingtalk_notification(market, contract, price, shares, reason):
    """发送钉钉交易机会通知 (增加启动静默期)"""
    if time.time() - _STARTUP_TIME < 60:
        logger.info(f"[钉钉] 启动静默期，忽略通知: {market} {reason}")
        return
        
    webhook = os.getenv("DINGTALK_WEBHOOK")
    if not webhook:
        logger.warning("钉钉 Webhook 未配置，跳过通知")
        return
    
    total_cost = price * shares
    
    # 消息需要包含关键词 "Polymarket"
    message = f"""🚨 Polymarket 交易触发提醒

📍 市场: {market}
🎯 目标合约: {contract}
💰 买入单价: {price:.3f} USDC
持有份额: {shares:.1f}
总计成本: {total_cost:.2f} USDC
📝 触发理由: {reason}
⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

请及时关注实盘动态！"""
    
    payload = {
        "msgtype": "text",
        "text": {"content": message}
    }
    
    try:
        resp = requests.post(webhook, json=payload, timeout=5)
        if resp.status_code == 200:
            logger.info(f"[钉钉] 通知发送成功")
        else:
            logger.warning(f"[钉钉] 通知发送失败: {resp.text}")
    except Exception as e:
        logger.error(f"[钉钉] 通知发送异常: {e}")


class WeatherBot:
    """并行多区域交易机器人"""
    
    def __init__(self):
        self.config = QuantConfig
        self.executor = PolyExecutor(self.config)
        self.pos_manager = PositionManager()
        
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

        unit = conf.get("unit", "C")
        
        while True:
            try:
                # 检查日期，如果跨天则刷新 slug
                now_date_str = self._get_local_date(tz_offset)
                if now_date_str != current_date_str:
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
                    state.max_temp_overall = max(state.max_temp_overall, state.noaa_curr)
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
                
                v_fit = (state.om_curr * self.config.W1_OM + state.mn_curr * self.config.W2_MN) if (state.om_curr and state.mn_curr) else None
                if v_fit:
                    state.update_v_fit(v_fit)
                
                for hist in [state.om_history, state.mn_history, state.v_fit_history]:
                    if len(hist) > 10: hist.pop(0)

                # 3. 策略决策
                state.market_prices = prices # 存入全量报价
                
                # --- 新逻辑：NOAA 下跌触发 / 17点强买 ---
                signal, reason, target_temp = StrategyKernel.calculate_noaa_drop_signal(
                    state, self.config, state.max_temp_overall if state.max_temp_overall > -900 else None, state.has_traded_today
                )
                
                # 兼容原有 v_fit 显示
                logger.info(f"[{preset_name:8}] NOAA: {state.noaa_curr if state.noaa_curr else 0.0:.1f} | Max: {state.max_temp_overall if state.max_temp_overall > -900 else 0.0:.1f} | Status: {signal:5} | Reason: {reason}")

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
                    
                    # [Rule] 通用价格滤网：无论何种触发模式，价格必须 > 0.5
                    # 这是为了防止在极端概率下（如气温虽然跌了但仍有变数）买入垃圾合约
                    should_execute = True
                    price_val = float(contract_price) if contract_price else 0.0
                    
                    if price_val <= 0.5:
                        should_execute = False
                        reason_prefix = "Force buy" if signal == 'BUY_FORCE' else "Drop buy"
                        reason = f"{reason_prefix} skipped: Price {price_val} <= 0.5"
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
                
                self._record_data(current_recording_file, state, prices, signal, reason)

            except Exception as e:
                logger.error(f"[{preset_name}] Loop error: {e}")
                
            await asyncio.sleep(interval)

    def _get_local_date(self, offset):
        """获取站点本地日期字符串 (YYYY-MM-DD)"""
        import datetime as dt
        from datetime import timezone, timedelta
        utc_now = dt.datetime.now(timezone.utc)
        return (utc_now + timedelta(hours=offset)).strftime("%Y-%m-%d")

    def _record_outcome(self, preset_name, date_str, slug_id, state):
        """结算并记录每日最终结果 (Outcome)"""
        data_dir = "data/outcomes"
        os.makedirs(data_dir, exist_ok=True)
        filename = f"{data_dir}/outcome_{preset_name}.csv"
        
        file_exists = os.path.isfile(filename)
        with open(filename, 'a', newline='') as f:
            fieldnames = ['date', 'slug_id', 'noaa_max']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            
            # 记录最高温
            actual_max = f"{state.max_temp_overall:.2f}" if state.max_temp_overall > -900 else "N/A"
            
            writer.writerow({
                'date': date_str,
                'slug_id': slug_id,
                'noaa_max': actual_max
            })
            logger.info(f"[✓] Daily Summary Saved for {preset_name} | Max(NOAA): {actual_max}")
            
        # 重置最高温以便第二天重新开始
        state.max_temp_overall = -999.0

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
        filename = f"{data_dir}/trade_history_{city_name}.csv"
        
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

    def _record_data(self, filename, state, prices, signal, reason):
        """记录实时数据到 CSV (全量原始记录，平铺报价列)"""
        if prices:
            logger.info(f"[DEBUG] Recording {len(prices)} price brackets to {filename}")
        else:
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
            'reason': reason
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
                'signal', 'reason'
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
        """每隔 4 小时更新一次状态并发送钉钉汇总报告"""
        logger.info(f"[*] Postion Monitor Loop Started (Interval: {report_interval_hours}h)")
        
        while True:
            # 首先等待间隔时间，避免启动时立即推送
            await asyncio.sleep(report_interval_hours * 3600)
            try:
                # 1. 更新所有地点的持仓状态
                for p in presets:
                    self.pos_manager.update_positions_status(p)
                
                # 2. 生成汇总报告并发送
                report_text = self.pos_manager.get_summary_report()
                
                webhook = os.getenv("DINGTALK_WEBHOOK")
                if webhook:
                    payload = {
                        "msgtype": "markdown",
                        "markdown": {
                            "title": "Polymarket 持仓报告",
                            "text": report_text
                        }
                    }
                    requests.post(webhook, json=payload, timeout=10)
                    logger.info("[监控] 已发送每 4 小时持仓汇总报告")
                else:
                    logger.warning("[监控] 钉钉 Webhook 未配置，无法发送汇总报告")

            except Exception as e:
                logger.error(f"[监控] 报告循环异常: {e}")


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
    
    args = parser.parse_args()
    
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
