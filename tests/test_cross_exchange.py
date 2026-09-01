import pytest
import asyncio
from strategies.cross_exchange import CrossExchangeArbitrageStrategy
from core.inventory_manager import CrossExchangeInventoryManager
from core.fee_calculator import FeeCalculator
from core.orderbook_manager import OrderBookManager, OrderBook
from core.execution_engine import ExecutionStateMachine, ExecutionContext, ExecutionState
from core.state_store import InMemoryStateStore
from core.notifier import ConsoleNotifier
from core.logger import ExecutionLogger
from paper_trading.simulator import SimulatedExchangeClient

@pytest.fixture
def cross_env():
    obm = OrderBookManager()
    
    # Binance is cheap (Buy here)
    obm.books[("binance", "BTCUSDT")] = OrderBook(symbol="BTCUSDT", exchange="binance", bids=[], asks=[(50000.0, 1.0)])
    
    # Kraken is expensive (Sell here)
    obm.books[("kraken", "BTCUSDT")] = OrderBook(symbol="BTCUSDT", exchange="kraken", bids=[(50500.0, 1.0)], asks=[])
    
    client_binance = SimulatedExchangeClient("binance", obm, simulated_latency_ms=0)
    client_kraken = SimulatedExchangeClient("kraken", obm, simulated_latency_ms=0)
    
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    logger = ExecutionLogger()
    sm = ExecutionStateMachine(store, notifier, logger)
    fee_calc = FeeCalculator({})
    inv_manager = CrossExchangeInventoryManager(store, {})
    
    config = {
        'exchanges': ['binance', 'kraken'],
        'min_profit_threshold_pct': 0.25,
        'withdrawal_fee_usd': 5.0
    }
    
    strategy = CrossExchangeArbitrageStrategy(
        exchange_clients={'binance': client_binance, 'kraken': client_kraken},
        fee_calculator=fee_calc,
        orderbook_manager=obm,
        state_machine=sm,
        inventory_manager=inv_manager,
        config=config
    )
    
    return strategy, client_binance, client_kraken, sm, store, inv_manager

@pytest.mark.asyncio
async def test_cross_exchange_evaluate_viable(cross_env):
    strategy, _, _, _, _, _ = cross_env
    
    result = await strategy.evaluate_opportunity("BTCUSDT", "binance", "kraken", 0.5)
    
    assert result['is_viable'] is True
    # Profit: (50500 - 50000) * 0.5 = 250 gross profit
    assert result['gross_pnl'] == 250.0

@pytest.mark.asyncio
async def test_cross_exchange_execute_parallel_success(cross_env):
    strategy, client_binance, client_kraken, sm, store, inv_manager = cross_env
    
    result = await strategy.evaluate_opportunity("BTCUSDT", "binance", "kraken", 0.5)
    context = ExecutionContext(execution_id="cross_1", strategy="cross_exchange")
    
    await strategy.execute_arbitrage(context, result['legs'])
    
    assert context.state == ExecutionState.COMPLETED
    assert len(context.data["filled_legs"]) == 2
    
    # Check balances simulating inventory movement
    binance_bals = await client_binance.get_balances()
    kraken_bals = await client_kraken.get_balances()
    
    # Binance bought 0.5 BTC
    assert binance_bals["BTC"] == 10.5
    # Kraken sold 0.5 BTC
    assert kraken_bals["BTC"] == 9.5

@pytest.mark.asyncio
async def test_cross_exchange_execute_parallel_partial_failure(cross_env):
    strategy, client_binance, client_kraken, sm, store, inv_manager = cross_env
    
    # Force Kraken to fail
    client_kraken.error_prob = 1.0
    
    result = await strategy.evaluate_opportunity("BTCUSDT", "binance", "kraken", 0.5)
    context = ExecutionContext(execution_id="cross_2", strategy="cross_exchange")
    
    await strategy.execute_arbitrage(context, result['legs'])
    
    # Because Kraken fails, Binance leg should be unwound
    assert context.state == ExecutionState.UNWOUND

@pytest.mark.asyncio
async def test_inventory_skew(cross_env):
    strategy, client_binance, client_kraken, sm, store, inv_manager = cross_env
    
    # Mock some expected balances in state store indicating a massive skew
    await store.set_expected_balances("binance", {"BTC": 90.0})
    await store.set_expected_balances("kraken", {"BTC": 10.0})
    
    # Ideal is 50/50. Binance has 90%, Kraken has 10%. Threshold is 20% diff.
    is_skewed = await inv_manager.check_skew(["binance", "kraken"], "BTC")
    assert is_skewed is True
    
    rec = await inv_manager.recommend_rebalance(["binance", "kraken"], "BTC", {"binance": 5.0})
    assert rec is not None
    assert rec["from"] == "binance"
    assert rec["to"] == "kraken"
    assert rec["amount"] == 40.0 # 90 - 50
