from typing import Dict
from core.state_store import StateStore
from core.logger import logging

logger = logging.getLogger("InventoryManager")
logger.setLevel(logging.INFO)

class CrossExchangeInventoryManager:
    """
    Section 14: Cross-Exchange Inventory Manager
    Tracks balances, forecasts skews, and enforces buffers.
    """
    def __init__(self, state_store: StateStore, config: Dict):
        self.state_store = state_store
        self.rebalance_skew_threshold_pct = config.get('rebalance_skew_threshold_pct', 20.0)
        
    async def get_total_inventory(self, asset: str) -> Dict[str, float]:
        """
        Calculates total inventory for a specific asset across all tracked exchanges.
        Returns { 'total': float, 'exchanges': { 'exchange_a': float, ... } }
        """
        total = 0.0
        balances = {}
        # We assume the reconciliation engine updates the expected balances in state_store.
        # But we need to know which exchanges. Let's assume we have them in config.
        exchanges = getattr(self, 'exchanges', ['binance', 'kraken'])
        for ex in exchanges:
            bals = await self.state_store.get_expected_balances(ex)
            amt = bals.get(asset, 0.0)
            balances[ex] = amt
            total += amt
        return {'total': total, 'exchanges': balances}

    def get_transfer_routes(self):
        # In a real environment, query exchange withdrawal status API.
        # As per rules, we don't enable withdrawal permissions, so we just return supported static routes.
        return [
            {"asset": "USDT", "from": "binance", "to": "kraken", "network": "TRC20", "fee": 1.0},
            {"asset": "USDT", "from": "kraken", "to": "binance", "network": "TRC20", "fee": 1.0}
        ]

    async def rebalance(self, notifier=None):
        # We cannot execute withdrawals (safety rule: no withdrawal keys)
        # So we identify imbalances and log alerts for manual rebalancing.
        if notifier:
            await notifier.send_high_priority_alert("MANUAL REBALANCE REQUIRED: Inventory skew threshold exceeded")

    async def check_skew(self, exchanges: list, asset: str) -> bool:
        """
        Checks if the inventory is skewed beyond the threshold.
        """
        total = 0.0
        balances = {}
        for exchange in exchanges:
            bals = await self.state_store.get_expected_balances(exchange)
            amt = bals.get(asset, 0.0)
            balances[exchange] = amt
            total += amt
            
        if total == 0:
            return False
            
        is_skewed = False
        for ex, amt in balances.items():
            pct = (amt / total) * 100.0
            # E.g., if we want 50/50 split across 2 exchanges, 
            # perfectly balanced is 50%. A skew threshold of 20% means
            # we alert if an exchange has < 30% or > 70%.
            ideal_pct = 100.0 / len(exchanges)
            if abs(pct - ideal_pct) > self.rebalance_skew_threshold_pct:
                logger.warning(f"Inventory skew detected on {ex} for {asset}: {pct:.1f}% of total (ideal: {ideal_pct:.1f}%)")
                is_skewed = True
                
        return is_skewed

    async def recommend_rebalance(self, exchanges: list, asset: str, withdrawal_fees: Dict[str, float]):
        """
        Recommends a rebalance transfer if skewed.
        """
        if not await self.check_skew(exchanges, asset):
            return None
            
        # Basic logic: recommend moving from max to min
        balances = {}
        for exchange in exchanges:
            bals = await self.state_store.get_expected_balances(exchange)
            balances[exchange] = bals.get(asset, 0.0)
            
        max_ex = max(balances, key=balances.get)
        min_ex = min(balances, key=balances.get)
        
        # Calculate how much to move to restore balance
        total = sum(balances.values())
        ideal = total / len(exchanges)
        
        transfer_amt = balances[max_ex] - ideal
        fee = withdrawal_fees.get(max_ex, 0.0)
        
        logger.info(f"Rebalance Recommended: Move {transfer_amt:.4f} {asset} from {max_ex} to {min_ex}. Est fee: {fee}")
        
        return {
            "from": max_ex,
            "to": min_ex,
            "asset": asset,
            "amount": transfer_amt,
            "fee": fee
        }
