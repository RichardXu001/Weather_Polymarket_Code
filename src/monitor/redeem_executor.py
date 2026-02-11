import time
import logging
import json
import os
import random
from web3 import Web3
from typing import Dict, List, Optional
from engine.config import QuantConfig

logger = logging.getLogger("RedeemExecutor")

class RedeemExecutor:
    """
    负责执行链上赎回 (Redeem) 逻辑。
    支持 EOA 和 Proxy (Gnosis Safe) 钱包，处理标准市场和 NegRisk 市场。
    """
    
    def __init__(self, config: QuantConfig):
        self.config = config
        self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        self.funder = os.getenv("POLYMARKET_FUNDER") # Gnosis Safe 地址
        
        # 常量定义 (Polygon Mainnet)
        self.CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        self.USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        self.NEGRISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
        
        # ABIs (简化版)
        self.CTF_ABI = [
            {"name":"redeemPositions","type":"function","inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"outputs":[]},
            {"name":"balanceOf","type":"function","inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]}
        ]
        self.PROXY_ABI = [
            {"name":"execTransaction","type":"function","inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"},{"name":"data","type":"bytes"},{"name":"operation","type":"uint8"},{"name":"safeTxGas","type":"uint256"},{"name":"baseGas","type":"uint256"},{"name":"gasPrice","type":"uint256"},{"name":"gasToken","type":"address"},{"name":"refundReceiver","type":"address"},{"name":"signatures","type":"bytes"}],"outputs":[{"name":"","type":"bool"}]},
            {"name":"nonce","type":"function","inputs":[],"outputs":[{"name":"","type":"uint256"}]}
        ]
        self.NEGRISK_ABI = [
            {"name":"redeemPositions","type":"function","inputs":[{"name":"conditionId","type":"bytes32"},{"name":"amounts","type":"uint256[]"}],"outputs":[]}
        ]

    def _get_w3(self) -> Optional[Web3]:
        """多 RPC 容灾连接"""
        nodes = [
            "https://polygon-rpc.com",
            "https://rpc.ankr.com/polygon",
            "https://polygon.llamarpc.com"
        ]
        for node in nodes:
            try:
                w3 = Web3(Web3.HTTPProvider(node, request_kwargs={'timeout': 10}))
                if w3.is_connected():
                    return w3
            except: continue
        return None

    def execute_redeem(self, slug: str, condition_id: str, outcome_index: int, is_negrisk: bool = False) -> bool:
        """
        执行单个市场的赎回操作
        """
        if self.config.DRY_RUN:
            logger.warning(f"[DRY RUN] Simulating redeem for {slug} (Index: {outcome_index})")
            return True

        w3 = self._get_w3()
        if not w3 or not self.private_key:
            logger.error("Redeem failed: No RPC connection or Private Key")
            return False

        try:
            account = w3.eth.account.from_key(self.private_key)
            eoa_address = account.address
            proxy_address = self.funder
            
            wallet = proxy_address if proxy_address else eoa_address
            is_proxy = True if proxy_address else False
            
            logger.info(f"🚀 Initializing redeem for {slug} | Wallet: {wallet}")

            # 1. 构造内部交易 Data
            if is_negrisk:
                neg_contract = w3.eth.contract(address=Web3.to_checksum_address(self.NEGRISK_ADAPTER), abi=self.NEGRISK_ABI)
                # 需获取余额，此处简化逻辑，实操中需精确 amounts
                # 注意：具体金额获取逻辑在全面实施时补齐
                amounts = [0, 0] 
                # ... 获取持仓余额的逻辑 ...
                return False # 待进一步细化金额获取
            else:
                ctf_contract = w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=self.CTF_ABI)
                inner_data = ctf_contract.encode_abi("redeemPositions", [
                    Web3.to_checksum_address(self.USDC_E), 
                    "0x" + "0"*64, 
                    condition_id, 
                    [1 << outcome_index]
                ])
                inner_to = self.CTF_ADDRESS

            # 2. 发起交易
            gas_price = int(w3.eth.gas_price * 1.5)
            if is_proxy:
                proxy_contract = w3.eth.contract(address=Web3.to_checksum_address(proxy_address), abi=self.PROXY_ABI)
                nonce = proxy_contract.functions.nonce().call()
                sig = "0x000000000000000000000000" + eoa_address[2:].lower() + "0000000000000000000000000000000000000000000000000000000000000000" + "01"
                
                tx = proxy_contract.functions.execTransaction(
                    Web3.to_checksum_address(inner_to), 0, inner_data, 0, 0, 0, 0,
                    "0x0000000000000000000000000000000000000000",
                    "0x0000000000000000000000000000000000000000",
                    Web3.to_bytes(hexstr=sig)
                ).build_transaction({
                    'from': eoa_address, 'nonce': w3.eth.get_transaction_count(eoa_address),
                    'gas': 600000, 'gasPrice': gas_price, 'chainId': 137
                })
            else:
                tx = {
                    'to': Web3.to_checksum_address(inner_to),
                    'data': inner_data,
                    'from': eoa_address, 'nonce': w3.eth.get_transaction_count(eoa_address),
                    'gas': 400000, 'gasPrice': gas_price, 'chainId': 137
                }

            signed = w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            logger.info(f"✅ Redeem TX Sent: {tx_hash.hex()}")
            
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            return receipt.status == 1

        except Exception as e:
            logger.error(f"Redeem error: {e}")
            return False
