import pytest
import asyncio
from core.execution_engine import ExecutionStateMachine, ExecutionContext, ExecutionState
from core.state_store import InMemoryStateStore
from core.notifier import ConsoleNotifier
from core.logger import ExecutionLogger

@pytest.fixture
def state_machine():
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    logger = ExecutionLogger()
    return ExecutionStateMachine(store, notifier, logger), store

@pytest.mark.asyncio
async def test_execution_state_machine_happy_path(state_machine):
    machine, store = state_machine
    
    context = ExecutionContext(
        execution_id="test_exec_1",
        strategy="triangular"
    )
    
    assert context.state == ExecutionState.IDLE
    
    await machine.transition(context, ExecutionState.OPPORTUNITY_DETECTED)
    assert context.state == ExecutionState.OPPORTUNITY_DETECTED
    
    saved_context = await store.get_execution_state("test_exec_1")
    assert saved_context.state == ExecutionState.OPPORTUNITY_DETECTED
    
    await machine.transition(context, ExecutionState.VALIDATING)
    await machine.transition(context, ExecutionState.EXECUTING_LEG_1)
    await machine.transition(context, ExecutionState.EXECUTING_LEG_2)
    await machine.transition(context, ExecutionState.EXECUTING_LEG_3)
    await machine.transition(context, ExecutionState.CONFIRMING_FILLS)
    await machine.transition(context, ExecutionState.RECONCILING)
    await machine.transition(context, ExecutionState.COMPLETED, data_updates={"pnl": 1.5})
    
    assert context.state == ExecutionState.COMPLETED
    assert context.data["pnl"] == 1.5

@pytest.mark.asyncio
async def test_execution_state_machine_failure_path_alerts(state_machine, capsys):
    machine, store = state_machine
    
    context = ExecutionContext(
        execution_id="test_exec_2",
        strategy="cross_exchange"
    )
    
    await machine.transition(context, ExecutionState.EXECUTING_LEG_1)
    await machine.transition(context, ExecutionState.PARTIAL_FAILURE)
    await machine.transition(context, ExecutionState.UNWINDING)
    await machine.transition(context, ExecutionState.STUCK, data_updates={"reason": "exchange timeout on unwind"})
    
    assert context.state == ExecutionState.STUCK
    
    # Check alert was triggered
    captured = capsys.readouterr()
    assert "HIGH PRIORITY ALERT" in captured.out
    assert "test_exec_2" in captured.out
    assert "STUCK" in captured.out
