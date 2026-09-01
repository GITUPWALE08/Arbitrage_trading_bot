import pytest
import pytest_asyncio
import asyncio
from core.database import DatabaseStateStore
from core.execution_engine import ExecutionState

@pytest_asyncio.fixture
async def db_store():
    # Use in-memory SQLite for testing SQLAlchemy models
    store = DatabaseStateStore("sqlite+aiosqlite:///:memory:")
    await store.initialize_db()
    return store

@pytest.mark.asyncio
async def test_save_and_get_execution(db_store):
    store = db_store
    
    # Save a VALIDATING state
    await store.save_execution_state("exec_1", "triangular", ExecutionState.VALIDATING, {"test": "data"})
    
    active = await store.get_active_executions()
    assert len(active) == 1
    assert active[0]["execution_id"] == "exec_1"
    assert active[0]["state"] == "VALIDATING"
    
    # Move to COMPLETED, should no longer be active
    await store.save_execution_state("exec_1", "triangular", ExecutionState.COMPLETED, {"realized_profit": 15.0})
    
    active_now = await store.get_active_executions()
    assert len(active_now) == 0

@pytest.mark.asyncio
async def test_kill_switches_persistence(db_store):
    store = db_store
    
    # Not tripped initially
    ks = await store.get_kill_switch("global")
    assert ks is None
    
    # Trip it
    await store.set_kill_switch("global", None, True, "admin", "market down")
    
    ks2 = await store.get_kill_switch("global")
    assert ks2 is not None
    assert ks2["is_tripped"] is True
    assert ks2["reason"] == "market down"
    
    # Untrip it
    await store.set_kill_switch("global", None, False, "admin", "market back")
    
    ks3 = await store.get_kill_switch("global")
    assert ks3["is_tripped"] is False

@pytest.mark.asyncio
async def test_reconciliation_log(db_store):
    store = db_store
    
    # Just checking it doesn't throw
    await store.save_reconciliation_log(
        exchange="binance",
        expected={"BTC": 1.0},
        actual={"BTC": 0.9},
        discrepancies={"BTC": {"diff": 0.1}},
        severity="WARNING"
    )
    # Success
