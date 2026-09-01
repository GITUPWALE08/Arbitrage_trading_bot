from typing import Dict, Any, List
from core.state_store import StateStore
from core.notifier import Notifier
from core.logger import logging

logger = logging.getLogger("RiskManager")
logger.setLevel(logging.INFO)

class RiskManager:
    """
    Section 6.2: Kill Switches and Risk Controls.
    """
    def __init__(self, state_store: StateStore, notifier: Notifier, config: Dict[str, Any]):
        self.state_store = state_store
        self.notifier = notifier
        self.config = config

    async def check_kill_switches(self, strategy: str = None, exchanges: List[str] = None) -> bool:
        """
        Checked at the start of every cycle before any new opportunity is evaluated.
        Returns True if trading can proceed, False if a kill switch is active.
        """
        # 1. Global
        global_ks = await self.state_store.get_kill_switch('global')
        if global_ks and global_ks.get('is_tripped'):
            logger.warning(f"GLOBAL KILL SWITCH IS ACTIVE: {global_ks.get('reason')}")
            return False
            
        # 2. Per-strategy
        if strategy:
            strat_ks = await self.state_store.get_kill_switch('strategy', strategy)
            if strat_ks and strat_ks.get('is_tripped'):
                logger.warning(f"STRATEGY KILL SWITCH ACTIVE for {strategy}: {strat_ks.get('reason')}")
                return False
                
        # 3. Per-exchange
        if exchanges:
            for ex in exchanges:
                ex_ks = await self.state_store.get_kill_switch('exchange', ex)
                if ex_ks and ex_ks.get('is_tripped'):
                    logger.warning(f"EXCHANGE KILL SWITCH ACTIVE for {ex}: {ex_ks.get('reason')}")
                    return False
                    
        return True

    async def trip_kill_switch(self, scope: str, scope_value: str, tripped_by: str, reason: str):
        """
        Trips a kill switch and fires an immediate alert.
        """
        await self.state_store.set_kill_switch(
            scope=scope,
            scope_value=scope_value,
            is_tripped=True,
            tripped_by=tripped_by,
            reason=reason
        )
        
        target = scope_value if scope_value else "ALL"
        msg = f"🛑 {scope.upper()} KILL SWITCH TRIPPED for {target}\nReason: {reason}\nBy: {tripped_by}"
        logger.error(msg)
        await self.notifier.send_high_priority_alert(msg)

    async def untrip_kill_switch(self, scope: str, scope_value: str, untripped_by: str):
        """
        Clears a kill switch (manual intervention).
        """
        await self.state_store.set_kill_switch(
            scope=scope,
            scope_value=scope_value,
            is_tripped=False,
            tripped_by=untripped_by,
            reason="Manually cleared"
        )
        msg = f"✅ {scope.upper()} KILL SWITCH CLEARED for {scope_value or 'ALL'}\nBy: {untripped_by}"
        logger.info(msg)
        await self.notifier.send_high_priority_alert(msg)

    async def check_trade_limits(self, position_size_usd: float) -> bool:
        """
        Basic limit checks (Section 6.1).
        """
        max_size = self.config.get('max_position_size_usd', 1000.0)
        if position_size_usd > max_size:
            logger.warning(f"Trade size {position_size_usd} exceeds max {max_size}")
            return False
        return True
