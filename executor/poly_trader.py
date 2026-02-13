import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from engine.config import QuantConfig

logger = logging.getLogger("PolyExecutor")


@dataclass
class OrderSummary:
    status: Optional[str] = None
    filled_size: float = 0.0
    requested_size: float = 0.0
    avg_fill_price: float = 0.0
    raw: Optional[dict] = None
    terminal: bool = False
    filled: bool = False


def _coerce_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def summarize_order_state(order_data: dict, *, requested_size: Optional[float] = None) -> OrderSummary:
    """Normalize a CLOB order payload into a small, stable summary.

    Ported from IV_trading_Bot2 logic: defensive against schema differences.
    """
    if not isinstance(order_data, dict):
        return OrderSummary(status=None, filled_size=0.0, requested_size=float(requested_size or 0.0), raw=None)

    status = order_data.get("status") or order_data.get("state") or order_data.get("order_status")
    status_norm = str(status).upper() if status is not None else None

    filled = None
    for key in ("filled_size", "filledSize", "size_filled", "sizeFilled", "matched_size", "matchedSize", "size_matched"):
        if key in order_data:
            filled = _coerce_float(order_data.get(key))
            break

    remaining = None
    for key in ("remaining_size", "remainingSize", "size_remaining", "sizeRemaining"):
        if key in order_data:
            remaining = _coerce_float(order_data.get(key))
            break

    original = None
    for key in ("original_size", "originalSize", "size", "order_size", "orderSize"):
        if key in order_data:
            original = _coerce_float(order_data.get(key))
            break
    if original is None:
        original = float(requested_size or 0.0)

    if filled is None and remaining is not None and original is not None:
        filled = max(0.0, float(original) - float(remaining))
    if filled is None:
        filled = 0.0

    # Best-effort average fill price.
    avg_price = 0.0
    for key in ("avg_fill_price", "avgFillPrice", "average_price", "avgPrice", "price"):
        if key in order_data:
            v = _coerce_float(order_data.get(key))
            avg_price = float(v or 0.0)
            break

    return OrderSummary(
        status=status_norm,
        filled_size=float(filled or 0.0),
        requested_size=float(original or 0.0),
        avg_fill_price=avg_price,
        raw=order_data,
    )


class PolyExecutor:
    """Polymarket CLOB 交易执行器 (Dry-run + Real)."""

    def __init__(self, config: QuantConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except Exception as e:  # pragma: no cover
            raise RuntimeError("Missing dependency: py_clob_client (pip install -r requirements.txt)") from e

        private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
        if not private_key:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY is required for real trading")

        host = os.getenv("CLOB_HOST", "https://clob.polymarket.com").strip() or "https://clob.polymarket.com"
        signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))
        funder = os.getenv("POLYMARKET_FUNDER", "").strip() or None

        api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
        api_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
        api_passphrase = os.getenv("POLYMARKET_API_PASSPHRASE", "").strip()

        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
            self._client = ClobClient(
                host,
                key=private_key,
                chain_id=137,
                creds=creds,
                funder=funder,
                signature_type=signature_type,
            )
            logger.info("✅ CLOB client initialized with pre-configured API creds.")
        else:
            self._client = ClobClient(
                host,
                key=private_key,
                chain_id=137,
                signature_type=signature_type,
                funder=funder,
            )
            derived = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(derived)
            logger.info("✅ CLOB client initialized with derived API creds.")

        return self._client

    def _place_buy_order_sync(self, token_id: str, price: float, size: float, tif: str, *, neg_risk: bool = False) -> dict:
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY

        client = self._get_client()

        # Protocol guard: cap at 0.99 to avoid invalid orders around 1.0.
        capped_price = min(float(price), float(os.getenv("CLOB_PRICE_CAP", "0.99")))
        order_args = OrderArgs(token_id=str(token_id), price=capped_price, size=float(size), side=BUY)
        options = PartialCreateOrderOptions(neg_risk=bool(neg_risk))
        signed = client.create_order(order_args, options)
        ot = getattr(OrderType, (tif or "GTC").upper(), OrderType.GTC)
        return client.post_order(signed, ot)

    @staticmethod
    def _extract_order_id(result: dict) -> Optional[str]:
        if not isinstance(result, dict):
            return None
        for key in ("orderID", "orderId", "order_id", "id"):
            v = result.get(key)
            if v:
                return str(v)
        for key in ("order", "data", "result"):
            nested = result.get(key)
            if isinstance(nested, dict):
                oid = PolyExecutor._extract_order_id(nested)
                if oid:
                    return oid
        return None

    def get_order_sync(self, order_id: str) -> dict:
        client = self._get_client()
        return client.get_order(order_id)

    def cancel_orders_sync(self, order_ids: Sequence[str]) -> Optional[dict]:
        if not order_ids:
            return None
        client = self._get_client()
        # py_clob_client supports cancel_orders(list[str])
        return client.cancel_orders(list(order_ids))

    def wait_for_terminal_order_sync(
        self,
        order_id: str,
        *,
        requested_size: Optional[float] = None,
        timeout_seconds: float = 3.0,
        poll_interval_seconds: float = 0.25,
    ) -> OrderSummary:
        """Poll order state until it is terminal, filled, or timeout."""
        terminal_statuses = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}
        start = time.monotonic()
        last = None

        while (time.monotonic() - start) < timeout_seconds:
            try:
                od = self.get_order_sync(order_id)
                last = summarize_order_state(od, requested_size=requested_size)
            except Exception as exc:
                last = OrderSummary(status="ERROR", filled_size=0.0, requested_size=float(requested_size or 0.0), raw={"error": str(exc)})

            status = str(last.status or "").upper()
            filled = float(last.filled_size or 0.0)
            if requested_size is not None and filled + 1e-9 >= float(requested_size):
                last.terminal = True
                last.filled = True
                return last

            if status in terminal_statuses:
                last.terminal = True
                last.filled = (status == "FILLED")
                return last

            time.sleep(poll_interval_seconds)

        if last is None:
            last = OrderSummary(status=None, filled_size=0.0, requested_size=float(requested_size or 0.0), raw=None)
        last.terminal = False
        last.filled = False
        return last

    def get_order_summary_sync(self, order_id: str, *, requested_size: Optional[float] = None) -> OrderSummary:
        if self.config.DRY_RUN:
            return OrderSummary(status="FILLED", filled_size=float(requested_size or 0.0), requested_size=float(requested_size or 0.0), terminal=True, filled=True)
        od = self.get_order_sync(order_id)
        return summarize_order_state(od, requested_size=requested_size)

    async def get_order_summary(self, order_id: str, *, requested_size: Optional[float] = None) -> OrderSummary:
        if self.config.DRY_RUN:
            return OrderSummary(status="FILLED", filled_size=float(requested_size or 0.0), requested_size=float(requested_size or 0.0), terminal=True, filled=True)

        od = await asyncio.to_thread(self.get_order_sync, order_id)
        return summarize_order_state(od, requested_size=requested_size)

    async def execute_trade(self, signal: str, token_id: str, price: float, amount: float, *, neg_risk: bool = False) -> Dict[str, Any]:
        """
        Execute a BUY against a specific CLOB token id.
        Returns:
          {success, order_id, filled_shares, avg_price, status, is_dry_run}
        """
        if signal != "BUY":
            return {"success": False, "order_id": None, "filled_shares": 0.0, "avg_price": 0.0, "status": "IGNORED", "is_dry_run": self.config.DRY_RUN}

        if self.config.DRY_RUN:
            oid = f"sim_{int(time.time())}"
            logger.warning(f"[DRY RUN] Simulating BUY {amount} @ {price} (token={str(token_id)[-6:]})")
            return {"success": True, "order_id": oid, "filled_shares": float(amount), "avg_price": float(price), "status": "FILLED", "is_dry_run": True}

        tif = os.getenv("ORDER_TYPE", "GTC").upper()
        try:
            result = await asyncio.to_thread(self._place_buy_order_sync, token_id, price, amount, tif, neg_risk=neg_risk)
            oid = self._extract_order_id(result)
            if not oid:
                # Order might still be accepted; surface raw payload for debugging.
                logger.error(f"CLOB order placed but order_id not found in response: {result}")
                return {"success": False, "order_id": None, "filled_shares": 0.0, "avg_price": 0.0, "status": "NO_ORDER_ID", "is_dry_run": False, "raw": result}

            # Poll briefly for terminal status (IV_bot reference logic).
            poll_timeout = float(os.getenv("ORDER_POLL_TIMEOUT_SECONDS", "3.0"))
            poll_interval = float(os.getenv("ORDER_POLL_INTERVAL_SECONDS", "0.25"))
            summary = await asyncio.to_thread(
                self.wait_for_terminal_order_sync,
                oid,
                requested_size=float(amount),
                timeout_seconds=poll_timeout,
                poll_interval_seconds=poll_interval,
            )
            return {
                "success": True,
                "order_id": oid,
                "filled_shares": float(summary.filled_size or 0.0),
                "avg_price": float(summary.avg_fill_price or 0.0),
                "status": summary.status or "SUBMITTED",
                "is_dry_run": False,
                "raw": result,
            }
        except Exception as e:
            logger.error(f"Execution Error: {e}")
            return {"success": False, "order_id": None, "filled_shares": 0.0, "avg_price": 0.0, "status": "ERROR", "is_dry_run": False, "error": str(e)}
