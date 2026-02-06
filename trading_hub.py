import asyncio
import logging
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

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TradingHub")

def send_dingtalk_notification(market, contract, price, v_fit, reason):
    """发送钉钉交易机会通知"""
    webhook = os.getenv("DINGTALK_WEBHOOK")
    if not webhook:
        logger.warning("钉钉 Webhook 未配置，跳过通知")
        return
    
    # 消息需要包含关键词 "Polymarket"
    message = f"""🚨 Polymarket 交易机会提醒

📍 市场: {market}
🎯 目标合约: {contract}
💰 当前报价: {price:.3f}
📊 V_fit 拟合值: {v_fit:.2f}
📝 触发理由: {reason}
⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

请及时关注市场动态！"""
    
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


class TradingHub:
    """并行多区域交易枢纽"""
    
    def __init__(self):
        self.config = QuantConfig
        self.executor = PolyExecutor(self.config)
        
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

        while True:
            try:
                # 检查是否跨天 (基于站点本地日期)
                new_date_str = self._get_local_date(tz_offset)
                if new_date_str != current_date_str:
                    logger.info(f"[*] Date rolled at {preset_name}: {current_date_str} -> {new_date_str}. Switching Market...")
                    
                    # 结算前一日结果 (Outcome)
                    self._record_outcome(preset_name, current_date_str, slug, state)
                    
                    current_date_str = new_date_str
                    slug = self._get_dynamic_slug(conf['slug_template'], tz_offset)
                    
                    # 跨天切换时同步更新文件名记录
                    session_start = datetime.now().strftime('%Y%m%d_%H%M')
                    current_recording_file = f"data/recordings/weather_recording_{preset_name}_{session_start}.csv"
                    
                    monitor = WeatherPriceMonitor(
                        conf["icao"], slug, conf["lat"], conf["lon"]
                    )
                    monitor.csv_file = current_recording_file
                    # 跨天后清空部分历史，防止受到前一天异常波动干扰
                    state.v_fit_history = []
                    logger.info(f"[+] Market Switched to: {slug}")

                # 1. 抓取数据
                loop = asyncio.get_event_loop()
                wd = await loop.run_in_executor(None, monitor.get_weather_data)
                prices = await loop.run_in_executor(None, monitor.fetch_polymarket_asks)
                
                # 2. 更新状态机
                state.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                state.local_hour, state.local_time = self._get_local_time_info(tz_offset)
                
                # 更新最高温观察值 (仅追踪官方源 NOAA 用于结算 Outcome)
                sources = wd['sources']
                state.noaa_curr = sources['NOAA (METAR)']['curr']
                state.om_curr = sources['Open-Meteo']['curr']
                state.om_fore = sources['Open-Meteo']['fore']
                state.mn_curr = sources['Met.no']['curr']
                state.mn_fore = sources['Met.no']['fore']
                
                state.consensus_curr = wd['avg_curr']
                state.consensus_fore = wd['avg_fore']
                
                if state.noaa_curr is not None:
                    state.max_temp_overall = max(state.max_temp_overall, state.noaa_curr)
                
                # 兼容旧版本 state 引用 (供策略使用)
                state.noaa_now = state.noaa_curr
                state.om_now = state.om_curr
                state.mn_now = state.mn_curr
                state.actual_now = state.consensus_curr
                state.forecast_1h = state.consensus_fore
                
                if state.om_curr is not None: state.om_history.append(state.om_curr)
                if state.mn_curr is not None: state.mn_history.append(state.mn_curr)
                
                v_fit = (state.om_now * self.config.W1_OM + state.mn_now * self.config.W2_MN) if (state.om_now and state.mn_now) else None
                if v_fit:
                    state.update_v_fit(v_fit)
                
                for hist in [state.om_history, state.mn_history, state.v_fit_history]:
                    if len(hist) > 10: hist.pop(0)

                # 3. 决策
                state.target_temp = conf.get("target_temp", 8.0) 
                state.market_prices = prices # 存入全量报价
                signal, reason, meta = StrategyKernel.calculate_strategy_signals(state, self.config)
                
                logger.info(f"[{preset_name:8}] V_fit: {v_fit if v_fit else 0.0:.2f} | Status: {signal:5} | Reason: {reason}")

                # 4. 执行决策与记录 (买入时取特定的 best_ask，但录制会录制所有)
                if signal == 'BUY':
                    best_ask = list(prices.values())[0] if prices else 0.5
                    # 找到与 v_fit 匹配的合约及其价格
                    from engine.models import WeatherModel
                    target_contract = WeatherModel.predict_noaa(v_fit) if v_fit else state.target_temp
                    contract_price = prices.get(f"price_{int(target_contract)}°C", best_ask)
                    
                    # 发送钉钉通知
                    send_dingtalk_notification(
                        market=preset_name.upper(),
                        contract=f"{target_contract}°C",
                        price=contract_price,
                        v_fit=v_fit if v_fit else 0.0,
                        reason=reason
                    )
                    
                    await self.executor.execute_trade(signal, monitor.event_slug, best_ask, 100)

                
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
            fieldnames = ['date', 'slug_id', 'target_threshold', 'noaa_max', 'result']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            
            # 判定结果 (基于 target_temp 和 NOAA 最高温)
            if state.max_temp_overall <= -900:
                result = "DATA_MISSING"
                actual_max = "N/A"
            else:
                result = "UNDER" if state.max_temp_overall <= state.target_temp else "OVER"
                actual_max = f"{state.max_temp_overall:.2f}"
            
            writer.writerow({
                'date': date_str,
                'slug_id': slug_id,
                'target_threshold': state.target_temp,
                'noaa_max': actual_max,
                'result': result
            })
            logger.info(f"[✓] Outcome Saved for {preset_name} | Slug: {slug_id} | Max(NOAA): {actual_max} | Result: {result}")
            
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

    def _record_data(self, filename, state, prices, signal, reason):
        """记录实时数据到 CSV (全量原始记录，平铺报价列)"""
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
        
        # 将所有报价平铺到 row 中
        price_cols = []
        if prices:
            for title, ask in prices.items():
                col_name = f"price_{title}"
                row[col_name] = ask
                price_cols.append(col_name)

        with open(filename, 'a', newline='') as f:
            base_fields = [
                'timestamp', 'local_time', 'local_hour', 
                'noaa_curr', 'om_curr', 'om_fore', 'mn_curr', 'mn_fore',
                'signal', 'reason'
            ]
            
            # 如果是新文件，确定所有列名
            if not file_exists:
                fieldnames = base_fields + sorted(price_cols)
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            else:
                # 如果是已存在文件且有新报价列出现，DictWriter 会由于 extrasaction='ignore' 忽略新列
                # 这里简单处理：取当前行的所有 key。
                # 注意：标准 CSV 不支持中途增加列。因为我们每场比赛/重启都会换文件，所以通常列是固定的。
                # 读取第一行获取 header
                with open(filename, 'r') as fr:
                    reader = csv.reader(fr)
                    existing_headers = next(reader)
                writer = csv.DictWriter(f, fieldnames=existing_headers, extrasaction='ignore')
            
            writer.writerow(row)

    async def run_parallel(self, presets, interval=30):
        """并行运行多个 Preset"""
        logger.info(f"[*] Launching Multi-Location Engine: {presets}")
        logger.info(f"[*] Mode: {'DRY RUN' if self.config.DRY_RUN else 'REAL'}")
        
        tasks = [self.run_location_loop(p, interval) for p in presets]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    import argparse
    from weather_price_monitor import PRESETS

    parser = argparse.ArgumentParser(description="Polymarket 天气自动交易枢纽")
    parser.add_argument(
        "--presets", 
        nargs="+", 
        default=["london", "seoul"],
        choices=list(PRESETS.keys()),
        help="要运行的地点预设 (空格分隔，支持: london, seoul)"
    )
    parser.add_argument(
        "--interval", 
        type=int, 
        default=30, 
        help="采样间隔(秒)，默认 30s"
    )
    
    args = parser.parse_args()
    
    hub = TradingHub()
    try:
        asyncio.run(hub.run_parallel(args.presets, interval=args.interval))
    except KeyboardInterrupt:
        logger.info("[!] 收到退出信号，正在停止服务...")
