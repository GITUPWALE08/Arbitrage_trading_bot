import enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from datetime import datetime, timezone
from core.logger import ExecutionLogger
from core.notifier import Notifier

class ExecutionState(str, enum.Enum):
    IDLE = "IDLE"
    OPPORTUNITY_DETECTED = "OPPORTUNITY_DETECTED"
    VALIDATING = "VALIDATING"
    EXECUTING_LEG_1 = "EXECUTING_LEG_1"
    EXECUTING_LEG_2 = "EXECUTING_LEG_2"
    EXECUTING_LEG_3 = "EXECUTING_LEG_3"
    CONFIRMING_FILLS = "CONFIRMING_FILLS"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    UNWINDING = "UNWINDING"
    UNWOUND = "UNWOUND"
    STUCK = "STUCK"
    FAILED = "FAILED"

class ExecutionContext(BaseModel):
    """
    Holds the context of an execution attempt.
    """
    execution_id: str
    strategy: str
    state: ExecutionState = ExecutionState.IDLE
    created_at: datetime = datetime.now(timezone.utc)
    updated_at: datetime = datetime.now(timezone.utc)
    
    # Store arbitrary data for the execution (e.g., opportunity details, fill details)
    data: Dict[str, Any] = {}

class StateMachineError(Exception):
    pass

class ExecutionStateMachine:
    """
    Manages the state transitions for an execution attempt.
    Ensures every transition is persisted before returning.
    """
    def __init__(self, state_store, notifier: Notifier, logger: ExecutionLogger):
        """
        :param state_store: An instance of StateStore to persist state synchronously.
        :param notifier: An instance of Notifier for high-priority alerts.
        :param logger: An instance of ExecutionLogger for audit trails.
        """
        self.state_store = state_store
        self.notifier = notifier
        self.logger = logger

    async def transition(self, context: ExecutionContext, new_state: ExecutionState, data_updates: Optional[Dict[str, Any]] = None):
        """
        Transitions the execution to a new state and persists it.
        """
        old_state = context.state
        context.state = new_state
        context.updated_at = datetime.now(timezone.utc)
        
        if data_updates:
            context.data.update(data_updates)

        # 13. Logging: Log every state transition with timestamp
        self.logger.log_transition(context.execution_id, old_state.value, new_state.value, context.data)

        # 2.1 Rule: Every state transition is written to persistent storage synchronously before the next action executes.
        await self.state_store.save_execution_state(context)

        # 2.1 Rule: STUCK must trigger an immediate high-priority alert and halt further trading
        if new_state == ExecutionState.STUCK:
            alert_msg = f"Execution {context.execution_id} for strategy {context.strategy} is STUCK. Manual intervention required."
            await self.notifier.send_high_priority_alert(alert_msg)
            # In a full implementation, we'd also trip the exchange/strategy kill switch here.
            # We will implement the RiskManager / KillSwitch hook later in the build.
        
        return context
