import pytest
from core.risk_manager import RiskManager
from core.state_store import InMemoryStateStore
from core.notifier import ConsoleNotifier

@pytest.fixture
def risk_env():
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    config = {'max_position_size_usd': 500.0}
    rm = RiskManager(store, notifier, config)
    return rm, store

@pytest.mark.asyncio
async def test_risk_manager_clean_state(risk_env):
    rm, store = risk_env
    
    can_trade = await rm.check_kill_switches(strategy="triangular", exchanges=["binance", "kraken"])
    assert can_trade is True

@pytest.mark.asyncio
async def test_risk_manager_global_kill(risk_env):
    rm, store = risk_env
    
    await rm.trip_kill_switch('global', None, 'admin', 'market crash')
    
    can_trade = await rm.check_kill_switches(strategy="triangular", exchanges=["binance"])
    assert can_trade is False

@pytest.mark.asyncio
async def test_risk_manager_strategy_kill(risk_env):
    rm, store = risk_env
    
    await rm.trip_kill_switch('strategy', 'cross_exchange', 'reconciliation_engine', 'inventory lost')
    
    # Cross exchange should fail
    can_trade_cross = await rm.check_kill_switches(strategy="cross_exchange", exchanges=["binance"])
    assert can_trade_cross is False
    
    # Triangular should still pass
    can_trade_tri = await rm.check_kill_switches(strategy="triangular", exchanges=["binance"])
    assert can_trade_tri is True

@pytest.mark.asyncio
async def test_risk_manager_exchange_kill(risk_env):
    rm, store = risk_env
    
    await rm.trip_kill_switch('exchange', 'kraken', 'liquidation_monitor', 'api down')
    
    # Anything touching kraken should fail
    can_trade_kraken = await rm.check_kill_switches(strategy="triangular", exchanges=["binance", "kraken"])
    assert can_trade_kraken is False
    
    # Binance only should pass
    can_trade_binance = await rm.check_kill_switches(strategy="triangular", exchanges=["binance", "okx"])
    assert can_trade_binance is True

@pytest.mark.asyncio
async def test_trade_limits(risk_env):
    rm, store = risk_env
    
    assert await rm.check_trade_limits(400.0) is True
    assert await rm.check_trade_limits(600.0) is False
