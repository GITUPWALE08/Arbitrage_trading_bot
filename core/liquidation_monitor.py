import asyncio
from core.exchange_client import ExchangeClient
from core.notifier import Notifier
from core.execution_engine import ExecutionStateMachine, ExecutionState, ExecutionContext
from core.logger import logging

logger = logging.getLogger("LiquidationMonitor")
logger.setLevel(logging.INFO)

class LiquidationMonitor:
    """
    Dedicated subsystem for monitoring Strategy C liquidation risk per Section 15.
    """
    def __init__(
        self,
        exchange_client: ExchangeClient,
        notifier: Notifier,
        state_machine: ExecutionStateMachine,
        warning_threshold: float = 0.8,
        action_threshold: float = 0.9,
        poll_interval_sec: int = 30
    ):
        self.client = exchange_client
        self.notifier = notifier
        self.state_machine = state_machine
        self.warning_threshold = warning_threshold
        self.action_threshold = action_threshold
        self.poll_interval_sec = poll_interval_sec
        self.risk_manager = None
        self.active_positions = {} # execution_id -> context
        
    def add_position(self, context: ExecutionContext, symbol: str):
        self.active_positions[context.execution_id] = {"context": context, "symbol": symbol}
        
    def remove_position(self, execution_id: str):
        if execution_id in self.active_positions:
            del self.active_positions[execution_id]

    async def _check_position(self, execution_id: str, data: dict):
        # In a real implementation, this requires an exchange specific endpoint 
        # to fetch maintenance margin ratio for the specific symbol/account.
        # We simulate checking it here.
        symbol = data["symbol"]
        context = data["context"]
        
        try:
            margin_ratio = await self.client.get_margin_ratio(symbol)
            
            await self.state_machine.state_store.save_margin_monitoring(
                position_id=execution_id,
                exchange=self.client.exchange_name,
                symbol=symbol,
                margin_ratio=margin_ratio,
                liquidation_price=None
            )
            
            if margin_ratio > self.action_threshold:
                alert_msg = f"CRITICAL: Liquidation risk action threshold breached for {symbol} ({margin_ratio*100}%). Auto-closing."
                logger.error(alert_msg)
                await self.notifier.send_high_priority_alert(alert_msg)
                
                if self.risk_manager:
                    await self.risk_manager.trip_kill_switch('strategy', 'funding_rate', 'liquidation_monitor', alert_msg)
                
                # Route through state machine's unwind path (Section 15/2.1)
                await self.state_machine.transition(
                    context, 
                    ExecutionState.UNWINDING, 
                    data_updates={"reason": "liquidation_risk_unwind"}
                )
                self.remove_position(execution_id)
                
            elif margin_ratio > self.warning_threshold:
                alert_msg = f"WARNING: Liquidation risk warning threshold breached for {symbol} ({margin_ratio*100}%)."
                logger.warning(alert_msg)
                await self.notifier.send_high_priority_alert(alert_msg)
                
        except Exception as e:
            logger.error(f"Error checking margin for {symbol}: {e}")

    async def monitor_loop(self):
        while True:
            for exec_id, data in list(self.active_positions.items()):
                await self._check_position(exec_id, data)
            await asyncio.sleep(self.poll_interval_sec)
