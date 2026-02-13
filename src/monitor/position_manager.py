import os
import csv
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from datetime import timedelta
import requests

logger = logging.getLogger("PositionManager")

class PositionManager:
    """
    管理持仓的全生命周期:
    PENDING (下单未成交) -> FILLED (已持仓) -> WIN/LOSS (已结算) -> REDEEMED (已赎回)
    """
    
    def __init__(self, data_dir: str = "data/trades"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.gamma_api_url = "https://gamma-api.polymarket.com"

    def _get_trade_history_file(self, city_name: str) -> str:
        return f"{self.data_dir}/trade_history_{city_name.lower()}.csv"

    def record_pending_order(
        self,
        city_name: str,
        local_time: str,
        signal: str,
        slug: str,
        contract: str,
        price: float,
        shares: float,
        reason: str,
        order_id: str,
        is_dry_run: bool,
        *,
        yes_token_id: str = "",
        condition_id: str = "",
        outcome_index: str = "",
        neg_risk: str = "",
    ):
        """记录初始下单状态 (PENDING)"""
        filename = self._get_trade_history_file(city_name)
        file_exists = os.path.isfile(filename)

        row = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'local_time': local_time,
            'signal_type': signal,
            'contract_slug': slug,
            'target_asset': contract,
            'execution_price': f"{price:.3f}",
            'shares': shares,
            'reasoning': reason,
            'order_id': order_id,
            'status': 'PENDING',
            'is_dry_run': "TRUE" if is_dry_run else "FALSE",
            'payout': 0.0,
            'redeemed': "FALSE",
            # New columns (best-effort): allow mapping to on-chain redemption.
            'yes_token_id': str(yes_token_id or ""),
            'condition_id': str(condition_id or ""),
            'outcome_index': str(outcome_index or ""),
            'neg_risk': str(neg_risk or ""),
        }

        fieldnames = [
            'timestamp', 'local_time', 'signal_type', 'contract_slug', 'target_asset',
            'execution_price', 'shares', 'reasoning', 'order_id', 'status', 'is_dry_run',
            'payout', 'redeemed',
            'yes_token_id', 'condition_id', 'outcome_index', 'neg_risk',
        ]

        # Header/schema migration: if file exists but headers differ, rewrite with superset schema.
        if file_exists:
            try:
                with open(filename, 'r', encoding='utf-8') as fr:
                    reader = csv.DictReader(fr)
                    existing_fields = reader.fieldnames or []
                    if set(fieldnames) != set(existing_fields):
                        rows_old = list(reader)
                        tmp = f"{filename}.tmp.{os.getpid()}"
                        with open(tmp, 'w', newline='', encoding='utf-8') as fw:
                            w = csv.DictWriter(fw, fieldnames=fieldnames)
                            w.writeheader()
                            for r in rows_old:
                                w.writerow({k: r.get(k, "") for k in fieldnames})
                        os.replace(tmp, filename)
            except Exception as e:
                logger.warning(f"Schema migration skipped for {filename}: {e}")

        # 兼容性处理：如果文件已存在但 header 不同，则可能需要处理（此处简化为强制匹配或删除旧文件）
        if file_exists:
            with open(filename, 'r') as f:
                header = f.readline().strip().split(',')
                if 'status' not in header:
                    logger.warning(f"Old format detected in {filename}. Backing up and starting fresh.")
                    os.rename(filename, f"{filename}.bak")
                    file_exists = False

        with open(filename, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        logger.info(f"[{city_name}] 📝 Order recorded: {order_id} (PENDING)")

    def update_positions_status(self, city_name: str, order_fetcher=None):
        """轮询并更新该城市所有订单的状态

        order_fetcher: optional callable(order_id: str, requested_size: float) -> OrderSummary-like
          Used to reconcile real orders (PENDING -> FILLED) without guessing.
        """
        filename = self._get_trade_history_file(city_name)
        if not os.path.exists(filename):
            return

        rows = []
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            logger.error(f"Error reading trade history {filename}: {e}")
            return

        updated = False
        for row in rows:
            # 防御性点 1: 确保 status 字段存在
            if 'status' not in row:
                continue

            status = str(row.get('status', '')).upper()
            # 如果已标记 redeemed，则将状态推进到 REDEEMED（终态），保留 payout 以便计算盈亏。
            if status in {'WIN', 'LOSS'} and str(row.get('redeemed', 'FALSE')).upper() == 'TRUE':
                row['status'] = 'REDEEMED'
                updated = True
                continue

            if status == 'REDEEMED':
                continue

            # 1. 处理 PENDING -> FILLED (如果是 Dry Run 直接转 FILLED)
            if status == 'PENDING':
                if str(row.get('is_dry_run', 'FALSE')).upper() == 'TRUE':
                    row['status'] = 'FILLED'
                    updated = True
                else:
                    # 实盘：优先通过订单接口对账，避免永远卡住。
                    if order_fetcher:
                        oid = str(row.get("order_id", "")).strip()
                        try:
                            req = float(row.get("shares", 0) or 0)
                        except ValueError:
                            req = 0.0
                        if oid and req > 0:
                            try:
                                summary = order_fetcher(oid, req)
                            except TypeError:
                                # Backward compat: order_fetcher(order_id) -> summary
                                summary = order_fetcher(oid)
                            if summary:
                                filled = getattr(summary, "filled_size", None)
                                status_o = getattr(summary, "status", None)
                                try:
                                    filled_f = float(filled or 0.0)
                                except (TypeError, ValueError):
                                    filled_f = 0.0
                                if filled_f > 0:
                                    row["status"] = "FILLED"
                                    # 修正 shares 为实际成交份额，避免后续 PnL/赎回计算偏差。
                                    row["shares"] = f"{filled_f:.6f}"
                                    updated = True
                                elif status_o and str(status_o).upper() in {"CANCELED", "CANCELLED", "REJECTED", "FAILED", "EXPIRED"}:
                                    row["status"] = "FAILED"
                                    updated = True

                    # 实盘: 先尝试直接判定是否已结算（允许 PENDING -> WIN/LOSS 跳转，
                    # 以避免因为缺少订单回报接口导致永远卡在 PENDING）。
                    slug = row.get('contract_slug')
                    asset = row.get('target_asset')
                    if slug and asset:
                        outcome = self._check_market_resolution(slug, asset)
                        if outcome:
                            row['status'] = outcome  # WIN or LOSS
                            row['payout'] = 1.0 if outcome == 'WIN' else 0.0
                            updated = True
                            continue

                    # 可选：允许通过环境变量做“假定成交”，以解锁后续 WIN/LOSS 轮询。
                    assume_after = os.getenv("ASSUME_FILLED_AFTER_MINUTES", "").strip()
                    if assume_after:
                        try:
                            mins = float(assume_after)
                        except ValueError:
                            mins = None
                        if mins is not None:
                            ts = row.get('timestamp', '')
                            try:
                                created = datetime.strptime(str(ts).strip(), "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                created = None
                            if created and datetime.now() - created >= timedelta(minutes=mins):
                                row['status'] = 'FILLED'
                                updated = True

            # 2. 处理 FILLED -> WIN/LOSS
            status = str(row.get('status', '')).upper()
            if status == 'FILLED':
                # 防御性点 2: 确保 slug 和 target_asset 存在
                slug = row.get('contract_slug')
                asset = row.get('target_asset')
                if slug and asset:
                    outcome = self._check_market_resolution(slug, asset)
                    if outcome:
                        row['status'] = outcome # WIN or LOSS
                        row['payout'] = 1.0 if outcome == 'WIN' else 0.0
                        updated = True

        if updated:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                fieldnames = [
                    'timestamp', 'local_time', 'signal_type', 'contract_slug', 'target_asset',
                    'execution_price', 'shares', 'reasoning', 'order_id', 'status', 'is_dry_run',
                    'payout', 'redeemed',
                    'yes_token_id', 'condition_id', 'outcome_index', 'neg_risk',
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"[{city_name}] 🔄 Trade history updated.")

    def _check_market_resolution(self, slug: str, target_contract: str) -> Optional[str]:
        """检查市场是否已结算，并返回结果 (WIN/LOSS)"""
        try:
            url = f"{self.gamma_api_url}/events?slug={slug}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if not data: return None
                
                event = data[0]
                # Gamma 接口在部分已结算事件上 resolved 可能为 None，但 closed=True 且 outcomePrices 已二值化。
                if not (event.get('resolved') or event.get('closed')):
                    return None
                
                # 找到获胜的合约
                markets = event.get('markets', [])
                for m in markets:
                    title = m.get('groupItemTitle', m.get('question'))
                    if title and self._contract_title_match(title, target_contract):
                        p_data = self._normalize_outcome_prices(m.get('outcomePrices', []))
                        if not p_data or len(p_data) < 2:
                            return None
                        outcomes = m.get('outcomes', [])
                        if not isinstance(outcomes, list):
                            outcomes = []
                        # 兼容：Gamma 的 outcomes/outcomePrices 顺序不保证为 [Yes, No]。
                        idx_yes = None
                        idx_no = None
                        for i, o in enumerate(outcomes):
                            o_norm = str(o).strip().lower()
                            if o_norm == 'yes':
                                idx_yes = i
                            elif o_norm == 'no':
                                idx_no = i
                        if idx_yes is not None and idx_no is not None and idx_yes < len(p_data) and idx_no < len(p_data):
                            yes_price = self._safe_float(p_data[idx_yes])
                            no_price = self._safe_float(p_data[idx_no])
                        else:
                            # 回退到旧逻辑（仅在 outcomes 不可用时使用）
                            yes_price = self._safe_float(p_data[0])
                            no_price = self._safe_float(p_data[1])
                        if yes_price is None or no_price is None:
                            return None
                        # 仅在二值化结算后才判胜负，避免把未结算概率当结果。
                        if not self._is_binary_outcome(yes_price, no_price):
                            return None
                        return 'WIN' if yes_price > no_price else 'LOSS'
        except Exception as e:
            logger.error(f"Error checking resolution for {slug}: {e}")
        return None

    @staticmethod
    def _safe_float(v) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_outcome_prices(p_data):
        if isinstance(p_data, list):
            return p_data
        if isinstance(p_data, str):
            try:
                parsed = json.loads(p_data)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                return []
        return []

    @staticmethod
    def _is_binary_outcome(yes_price: float, no_price: float) -> bool:
        # 结算后通常会非常接近 1/0；这里给少量容差兼容不同精度。
        return (yes_price >= 0.999 and no_price <= 0.001) or (no_price >= 0.999 and yes_price <= 0.001) or \
               (yes_price >= 0.999 and no_price <= 0.01) or (no_price >= 0.999 and yes_price <= 0.01)

    @staticmethod
    def _contract_title_match(title: str, target_contract: str) -> bool:
        title_norm = str(title).strip().lower()
        target_norm = str(target_contract).strip().lower()
        return title_norm == target_norm or target_norm in title_norm

    def get_summary_report(self) -> str:
        """生成全局持仓汇总报告字段 (自动合并相同合约的持仓)"""
        if not os.path.exists(self.data_dir):
            return "📭 当前无活跃持仓或近期交易记录。"
            
        all_files = [f for f in os.listdir(self.data_dir) if f.startswith("trade_history_")]
        if not all_files:
            return "📭 当前无活跃持仓或近期交易记录。"
            
        report = "📊 Polymarket 持仓汇总报告\n"
        report += f"⏰ 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        
        # 聚合字典: key=(city, asset, status), value={'shares': float, 'total_cost': float}
        active_agg = {}
        settled_count = 0
        total_profit = 0.0
        
        for f in all_files:
            city = f.replace("trade_history_", "").replace(".csv", "").upper()
            try:
                with open(os.path.join(self.data_dir, f), 'r', encoding='utf-8') as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        # 极端防御：跳过缺少核心字段的行
                        if not row.get('status') or not row.get('shares'):
                            continue
                        # 汇总报告仅统计真实单，忽略 dry run 记录
                        if str(row.get('is_dry_run', 'FALSE')).upper() == 'TRUE':
                            continue
                            
                        status = row['status']
                        asset = row.get('target_asset', 'Unknown')
                        shares = float(row.get('shares', 0))
                        price = float(row.get('execution_price', 0))
                        
                        if status in ['PENDING', 'FILLED']:
                            key = (city, asset, status)
                            if key not in active_agg:
                                active_agg[key] = {'shares': 0.0, 'total_cost': 0.0}
                            active_agg[key]['shares'] += shares
                            active_agg[key]['total_cost'] += price * shares
                            
                        elif status in ['WIN', 'LOSS', 'REDEEMED']:
                            settled_count += 1
                            payout = float(row.get('payout', 0))
                            profit = (payout - price) * shares
                            total_profit += profit
                            redeem_tag = "✅ 已赎回" if row.get('redeemed') == 'TRUE' else "⚠️ 待赎回"
                            report += f"🏁 {city} 最终结果:\n"
                            # REDEEMED 时用 payout 反推 WIN/LOSS（payout=1.0 视为 WIN，否则视为 LOSS）
                            if status == 'REDEEMED':
                                inferred = 'WIN' if payout >= 0.999 else 'LOSS'
                                report += f"- 合约: {asset} | 结果: {inferred} (REDEEMED)\n"
                            else:
                                report += f"- 合约: {asset} | 结果: {status}\n"
                            report += f"- PnL: ${profit:+.2f} | {redeem_tag}\n\n"
            except Exception as e:
                logger.error(f"Error processing {f} for report: {e}")

        # 生成活跃持仓报告 (从聚合数据中)
        if active_agg:
            for (city, asset, status), data in active_agg.items():
                total_shares = data['shares']
                avg_price = data['total_cost'] / total_shares if total_shares > 0 else 0.0
                
                report += f"📍 {city}: {asset}\n"
                report += f"- 状态: {status} | 份额: {total_shares:.1f}\n"
                report += f"- 均价: ${avg_price:.3f} | ROI: 持有中\n\n"

        if not active_agg and settled_count == 0:
            return "📭 当前无活跃持仓或近期交易记录。"
            
        report += f"---\n💰 累计盈亏 (已结算): ${total_profit:+.2f}"
        return report

    def mark_redeemed_by_condition(self, condition_id: str, outcome_index: int) -> int:
        """Best-effort: mark matching WIN/LOSS rows as redeemed and advance to REDEEMED."""
        if not condition_id:
            return 0
        updated_rows = 0
        all_files = [f for f in os.listdir(self.data_dir) if f.startswith("trade_history_")]
        for f in all_files:
            path = os.path.join(self.data_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as fr:
                    reader = csv.DictReader(fr)
                    rows = list(reader)
                    fieldnames = reader.fieldnames or []
            except Exception:
                continue

            changed = False
            for row in rows:
                if str(row.get("is_dry_run", "FALSE")).upper() == "TRUE":
                    continue
                if str(row.get("status", "")).upper() not in {"WIN", "LOSS"}:
                    continue
                if str(row.get("redeemed", "FALSE")).upper() == "TRUE":
                    continue
                if str(row.get("condition_id", "")).strip() != str(condition_id).strip():
                    continue
                try:
                    idx = int(str(row.get("outcome_index", "")).strip() or "0")
                except ValueError:
                    continue
                if idx != int(outcome_index):
                    continue
                row["redeemed"] = "TRUE"
                row["status"] = "REDEEMED"
                changed = True
                updated_rows += 1

            if changed:
                # Ensure schema includes our superset.
                base = [
                    'timestamp', 'local_time', 'signal_type', 'contract_slug', 'target_asset',
                    'execution_price', 'shares', 'reasoning', 'order_id', 'status', 'is_dry_run',
                    'payout', 'redeemed',
                    'yes_token_id', 'condition_id', 'outcome_index', 'neg_risk',
                ]
                fn = base if set(base).issuperset(set(fieldnames or [])) else (fieldnames or base)
                with open(path, "w", newline="", encoding="utf-8") as fw:
                    w = csv.DictWriter(fw, fieldnames=fn)
                    w.writeheader()
                    for r in rows:
                        w.writerow({k: r.get(k, "") for k in fn})
        return updated_rows
