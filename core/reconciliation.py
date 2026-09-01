from typing import Dict, Any, List
import asyncio
from core.state_store import StateStore
from core.exchange_client import ExchangeClient
from core.notifier import Notifier
from core.logger import logging

logger = logging.getLogger("Reconciliation")
logger.setLevel(logging.INFO)

class ReconciliationManager:
    """
    Continually checks actual exchange balances against our internal state to identify discrepancies.
    """
    def __init__(
        self,
        state_store: StateStore,
        exchange_clients: Dict[str, ExchangeClient],
        notifier: Notifier,
        dust_tolerance: float = 1e-6,
        halt_threshold: float = 0.01  # E.g., 0.01 units of discrepancy
    ):
        self.state_store = state_store
        self.exchange_clients = exchange_clients
        self.notifier = notifier
        self.dust_tolerance = dust_tolerance
        self.halt_threshold = halt_threshold
        self.risk_manager = None # can be injected after init to avoid circular deps

    async def reconcile_exchange(self, exchange_name: str):
        """
        Runs a reconciliation pass for a single exchange.
        """
        client = self.exchange_clients.get(exchange_name)
        if not client:
            logger.error(f"No exchange client found for {exchange_name}")
            return
            
        try:
            actual_balances = await client.get_balances()
            expected_balances = await self.state_store.get_expected_balances(exchange_name)
        except Exception as e:
            logger.error(f"Failed to fetch balances for {exchange_name}: {e}")
            return

        discrepancies = {}
        severity = "OK"
        should_halt = False
        
        all_assets = set(actual_balances.keys()).union(set(expected_balances.keys()))
        
        for asset in all_assets:
            actual = actual_balances.get(asset, 0.0)
            expected = expected_balances.get(asset, 0.0)
            diff = abs(actual - expected)
            
            # Write point-in-time snapshot
            await self.state_store.save_balances_snapshot(exchange_name, asset, actual, "reconciliation")
            
            if diff > self.dust_tolerance:
                discrepancies[asset] = {
                    "expected": expected,
                    "actual": actual,
                    "diff": diff
                }
                
                if diff > self.halt_threshold:
                    severity = "CRITICAL"
                    should_halt = True
                elif severity != "CRITICAL":
                    severity = "WARNING"

        if discrepancies:
            await self.state_store.save_reconciliation_log(
                exchange=exchange_name,
                expected=expected_balances,
                actual=actual_balances,
                discrepancies=discrepancies,
                severity=severity
            )
            
            if should_halt:
                msg = f"CRITICAL RECONCILIATION DISCREPANCY on {exchange_name}: {discrepancies}. Auto-halting exchange."
                await self.notifier.send_high_priority_alert(msg)
                if self.risk_manager:
                    await self.risk_manager.trip_kill_switch('exchange', exchange_name, 'reconciliation_engine', msg)
                await self._halt_exchange(exchange_name)
            else:
                # Warning level alert
                msg = f"Reconciliation discrepancy (warning) on {exchange_name}: {discrepancies}."
                await self.notifier.send_high_priority_alert(msg)
                
        else:
            logger.info(f"Reconciliation passed for {exchange_name}. No discrepancies.")
            
    async def _halt_exchange(self, exchange_name: str):
        """
        Trigger the per-exchange kill switch (Section 6.2).
        For now, this just logs, but it will be wired into RiskManager later.
        """
        logger.warning(f"HALTING TRADING on {exchange_name} due to reconciliation failure.")
        # TODO: integrate with actual KillSwitch state

    async def run_periodic_reconciliation(self, interval_seconds: int = 60):
        """
        Continuously runs reconciliation in the background.
        """
        while True:
            for exchange in self.exchange_clients.keys():
                await self.reconcile_exchange(exchange)
            await asyncio.sleep(interval_seconds)

