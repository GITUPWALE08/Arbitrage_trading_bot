from typing import Dict, Any, List
import asyncio
from core.state_store import StateStore
from core.exchange_client import ExchangeClient
from core.notifier import Notifier
from core.execution_engine import ExecutionStateMachine, ExecutionState
from core.logger import logging

logger = logging.getLogger("Recovery")
logger.setLevel(logging.INFO)

class RecoveryManager:
    """
    Handles crash recovery on startup. Resolves any in-flight executions before normal operations resume.
    """
    def __init__(
        self,
        state_store: StateStore,
        state_machine: ExecutionStateMachine,
        exchange_clients: Dict[str, ExchangeClient],
        notifier: Notifier
    ):
        self.state_store = state_store
        self.state_machine = state_machine
        self.exchange_clients = exchange_clients
        self.notifier = notifier

    async def run_startup_recovery(self) -> bool:
        """
        Runs on every startup.
        1. Reads last known state.
        2. Resolves any in-flight executions.
        3. Returns True if recovery was successful and trading can resume.
        """
        active_executions = await self.state_store.get_active_executions()
        
        if not active_executions:
            report = "Startup Recovery: Clean state. No in-flight executions found."
            logger.info(report)
            await self.notifier.send_high_priority_alert(report)
            return True
            
        report_lines = [f"Startup Recovery: Found {len(active_executions)} in-flight execution(s)."]
        
        for context in active_executions:
            report_lines.append(f" - Exec [{context.execution_id}] stuck in {context.state.value}.")
            
            # Simple heuristic for recovery logic (will be expanded per strategy)
            # Fetch actual order status from the exchange to see if it filled
            # If filled, try to transition to RECONCILING or COMPLETED.
            # If unknown or partially filled, transition to UNWINDING or STUCK.
            
            # For this MVP implementation, we assume if we crash mid-execution, 
            # we default to transitioning to UNWINDING (to shed risk) or STUCK if that fails.
            
            try:
                # Transition to UNWINDING as a safe default to shed residual directional exposure
                await self.state_machine.transition(
                    context, 
                    ExecutionState.UNWINDING, 
                    data_updates={"recovery_action": "auto_unwind_initiated"}
                )
                report_lines.append(f"   -> Initiated UNWINDING for [{context.execution_id}].")
                
                # In a real scenario, we would then execute the unwind trades here and transition to UNWOUND
                
            except Exception as e:
                # If we fail to even unwind, it is STUCK
                await self.state_machine.transition(
                    context, 
                    ExecutionState.STUCK,
                    data_updates={"recovery_error": str(e)}
                )
                report_lines.append(f"   -> STUCK [{context.execution_id}]: {e}")
                
        # Send full recovery report
        report = "\n".join(report_lines)
        logger.info(report)
        await self.notifier.send_high_priority_alert(report)
        
        # If any executions ended up STUCK, we should not resume normal operations.
        # STUCK is a first class state requiring manual intervention.
        stuck_executions = [c for c in await self.state_store.get_active_executions() if c.state == ExecutionState.STUCK]
        
        if stuck_executions:
            logger.error("Startup Recovery: Some executions are STUCK. Manual intervention required. Haulting trading.")
            return False
            
        return True
