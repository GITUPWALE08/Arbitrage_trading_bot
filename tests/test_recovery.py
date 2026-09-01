import pytest
from core.recovery import RecoveryManager
from core.state_store import InMemoryStateStore
from core.execution_engine import ExecutionStateMachine, ExecutionContext, ExecutionState
from core.notifier import ConsoleNotifier
from core.logger import ExecutionLogger
from tests.test_reconciliation import DummyExchangeClient

@pytest.fixture
def recovery_env():
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    logger = ExecutionLogger()
    state_machine = ExecutionStateMachine(store, notifier, logger)
    client = DummyExchangeClient({})
    manager = RecoveryManager(
        state_store=store,
        state_machine=state_machine,
        exchange_clients={"binance": client},
        notifier=notifier
    )
    return manager, store, capsys

@pytest.mark.asyncio
async def test_recovery_clean_state(capsys):
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    logger = ExecutionLogger()
    state_machine = ExecutionStateMachine(store, notifier, logger)
    manager = RecoveryManager(store, state_machine, {}, notifier)
    
    can_resume = await manager.run_startup_recovery()
    
    assert can_resume is True
    captured = capsys.readouterr()
    assert "Clean state. No in-flight executions found." in captured.out

@pytest.mark.asyncio
async def test_recovery_with_inflight_execution(capsys):
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    logger = ExecutionLogger()
    state_machine = ExecutionStateMachine(store, notifier, logger)
    
    # Inject an in-flight execution
    context = ExecutionContext(execution_id="crash_exec", strategy="triangular", state=ExecutionState.EXECUTING_LEG_2)
    await store.save_execution_state(context)
    
    manager = RecoveryManager(store, state_machine, {}, notifier)
    can_resume = await manager.run_startup_recovery()
    
    # Since we default to UNWINDING in the MVP, it shouldn't be STUCK unless it errors out, 
    # but UNWINDING is not terminal, so it's technically still active.
    # Wait, in the MVP recovery logic, we transition to UNWINDING. Is UNWINDING terminal? No.
    # Wait, the recovery logic returns False if there are STUCK executions.
    # So if it transitions to UNWINDING successfully, it returns True.
    assert can_resume is True
    
    saved_context = await store.get_execution_state("crash_exec")
    assert saved_context.state == ExecutionState.UNWINDING
    assert saved_context.data["recovery_action"] == "auto_unwind_initiated"
    
    captured = capsys.readouterr()
    assert "Found 1 in-flight execution(s)." in captured.out
    assert "Initiated UNWINDING for [crash_exec]" in captured.out
