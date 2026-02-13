import json
import logging
import os
import random
import time
from typing import Dict, List, Optional

import httpx
from web3 import Web3

logger = logging.getLogger("RedeemWorker")


def _load_cache(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(str(x) for x in data)
    except Exception:
        pass
    return set()


def _save_cache(path: str, cache: set):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(list(cache)), f, indent=2)
    os.replace(tmp, path)


def _connect_w3(nodes: List[str]) -> Optional[Web3]:
    for node in nodes:
        if not node:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(node, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                logger.info(f"✅ Connected RPC: {node}")
                return w3
        except Exception:
            continue
    return None


def redeem_positions_from_data_api(
    *,
    cache_path: str = "data/trades/redeemed_positions.json",
    max_positions: int = 50,
) -> List[Dict]:
    """
    Redeem resolved & winning positions using Polymarket data-api + on-chain calls.

    Returns a list of redeemed entries:
      {"condition_id": "...", "outcome_index": 0, "wallet": "0x..", "tx_hash": "0x..", "neg_risk": bool}
    """
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    polygon_rpc = os.getenv("POLYGON_RPC_URL", "").strip()

    if not private_key:
        logger.error("POLYMARKET_PRIVATE_KEY missing; cannot redeem.")
        return []

    nodes = [
        polygon_rpc,
        "https://polygon-rpc.com",
        "https://rpc.ankr.com/polygon",
        "https://1rpc.io/matic",
        "https://polygon.llamarpc.com",
        "https://polygon.meowrpc.com",
    ]
    w3 = _connect_w3(nodes)
    if not w3:
        logger.error("Failed to connect to any Polygon RPC.")
        return []

    account = w3.eth.account.from_key(private_key)
    eoa = account.address
    wallets = [eoa]
    if funder and funder.lower() != eoa.lower():
        wallets.append(funder)

    CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
    NEGRISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

    CTF_ABI = [
        {
            "name": "redeemPositions",
            "type": "function",
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"},
            ],
            "outputs": [],
        },
        {
            "name": "balanceOf",
            "type": "function",
            "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
            "outputs": [{"name": "", "type": "uint256"}],
        },
    ]
    PROXY_ABI = [
        {
            "name": "execTransaction",
            "type": "function",
            "inputs": [
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "data", "type": "bytes"},
                {"name": "operation", "type": "uint8"},
                {"name": "safeTxGas", "type": "uint256"},
                {"name": "baseGas", "type": "uint256"},
                {"name": "gasPrice", "type": "uint256"},
                {"name": "gasToken", "type": "address"},
                {"name": "refundReceiver", "type": "address"},
                {"name": "signatures", "type": "bytes"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {"name": "nonce", "type": "function", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    ]
    NEGRISK_ABI = [
        {"name": "redeemPositions", "type": "function", "inputs": [{"name": "conditionId", "type": "bytes32"}, {"name": "amounts", "type": "uint256[]"}], "outputs": []}
    ]

    redeemed_cache = _load_cache(cache_path)
    redeemed: List[Dict] = []

    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    proxy_contract = w3.eth.contract(address=Web3.to_checksum_address(funder), abi=PROXY_ABI) if funder else None
    neg = w3.eth.contract(address=Web3.to_checksum_address(NEGRISK_ADAPTER), abi=NEGRISK_ABI)

    for wallet in wallets:
        is_proxy = bool(funder) and wallet.lower() == funder.lower()
        url = f"https://data-api.polymarket.com/positions?user={wallet}&resolved=true"
        try:
            positions = httpx.get(url, timeout=15).json()
        except Exception as e:
            logger.warning(f"Failed to fetch positions for {wallet}: {e}")
            continue

        if not isinstance(positions, list):
            continue

        # Redeem winners first.
        for pos in positions[:max_positions]:
            try:
                cond_id = pos.get("conditionId")
                idx = int(pos.get("outcomeIndex", 0))
                asset_id = pos.get("assetId")
                market_data = pos.get("market") or {}
                is_negrisk = bool(market_data.get("negRisk", False))
                redeemable = bool(pos.get("redeemable", False))
                cur_price = float(pos.get("curPrice", 0) or 0)
                collateral = str(pos.get("collateral", "") or "").lower()
            except Exception:
                continue

            if not cond_id:
                continue

            cache_key = f"{cond_id}_{idx}_{wallet}"
            if cache_key in redeemed_cache:
                continue

            # Only redeem if it is basically a winner / redeemable.
            if cur_price < 0.99 and not redeemable:
                continue

            # Check balance to avoid re-redeeming.
            try:
                bal = 1
                if asset_id:
                    bal = ctf.functions.balanceOf(Web3.to_checksum_address(wallet), int(asset_id)).call()
                if int(bal) == 0:
                    redeemed_cache.add(cache_key)
                    _save_cache(cache_path, redeemed_cache)
                    continue
            except Exception as e:
                logger.warning(f"balanceOf failed for {wallet}: {e}")
                continue

            # Build inner calldata.
            if is_negrisk:
                amounts = [0, 0]
                if idx < len(amounts):
                    amounts[idx] = int(bal)
                inner_to = NEGRISK_ADAPTER
                inner_data = neg.encode_abi("redeemPositions", [cond_id, amounts])
            else:
                token = USDC_NATIVE if "native" in collateral else USDC_E
                inner_to = CTF_ADDRESS
                inner_data = ctf.encode_abi(
                    "redeemPositions",
                    [Web3.to_checksum_address(token), "0x" + "0" * 64, cond_id, [1 << idx]],
                )

            # Submit tx (EOA or Proxy).
            try:
                gas_price = int(w3.eth.gas_price * 15 // 10)
                if is_proxy and proxy_contract:
                    sig = (
                        "0x000000000000000000000000"
                        + eoa[2:].lower()
                        + "0000000000000000000000000000000000000000000000000000000000000000"
                        + "01"
                    )
                    tx = proxy_contract.functions.execTransaction(
                        Web3.to_checksum_address(inner_to),
                        0,
                        inner_data,
                        0,
                        0,
                        0,
                        0,
                        "0x0000000000000000000000000000000000000000",
                        "0x0000000000000000000000000000000000000000",
                        Web3.to_bytes(hexstr=sig),
                    ).build_transaction(
                        {
                            "from": eoa,
                            "nonce": w3.eth.get_transaction_count(eoa),
                            "gas": 600000,
                            "gasPrice": gas_price,
                            "chainId": 137,
                        }
                    )
                else:
                    tx = {
                        "to": Web3.to_checksum_address(inner_to),
                        "data": inner_data,
                        "from": eoa,
                        "nonce": w3.eth.get_transaction_count(eoa),
                        "gas": 400000,
                        "gasPrice": gas_price,
                        "chainId": 137,
                    }

                signed = w3.eth.account.sign_transaction(tx, private_key)
                raw_tx = getattr(signed, "raw_transaction", getattr(signed, "rawTransaction", None))
                if not raw_tx:
                    raise RuntimeError("Signed tx missing raw bytes")
                tx_hash = w3.eth.send_raw_transaction(raw_tx)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
                if int(getattr(receipt, "status", 0)) != 1:
                    raise RuntimeError("redeem tx failed")

                redeemed_cache.add(cache_key)
                _save_cache(cache_path, redeemed_cache)
                redeemed.append(
                    {
                        "condition_id": cond_id,
                        "outcome_index": idx,
                        "wallet": wallet,
                        "tx_hash": tx_hash.hex(),
                        "neg_risk": is_negrisk,
                    }
                )
                logger.info(f"✅ Redeemed {cond_id[:10]}.. idx={idx} wallet={wallet[:8]}.. tx={tx_hash.hex()[:10]}..")

                # Throttle.
                time.sleep(random.uniform(8, 12))
            except Exception as e:
                logger.warning(f"Redeem failed for {cond_id[:10]}.. idx={idx}: {e}")
                time.sleep(random.uniform(2, 4))

    return redeemed

