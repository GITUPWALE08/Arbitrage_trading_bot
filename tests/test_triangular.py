import pytest
import asyncio
from strategies.triangular import TriangularArbitrageStrategy
from core.fee_calculator import FeeCalculator
from core.orderbook_manager import OrderBookManager, OrderBook
from core.execution_engine import ExecutionStateMachine, ExecutionContext, ExecutionState
from core.state_store import InMemoryStateStore
from core.notifier import ConsoleNotifier
from core.logger import ExecutionLogger
from paper_trading.simulator import SimulatedExchangeClient

@pytest.fixture
def strategy_env():
    obm = OrderBookManager()
    # USDT -> BTC -> ETH -> USDT
    obm.books[("binance", "BTCUSDT")] = OrderBook(symbol="BTCUSDT", exchange="binance", bids=[], asks=[(50000.0, 1.0)])
    obm.books[("binance", "ETHBTC")] = OrderBook(symbol="ETHBTC", exchange="binance", bids=[], asks=[(0.05, 10.0)])
    obm.books[("binance", "ETHUSDT")] = OrderBook(symbol="ETHUSDT", exchange="binance", bids=[(2600.0, 10.0)], asks=[])
    
    # 200 USDT / 50000 = 0.004 BTC
    # 0.004 BTC / 0.05 = 0.08 ETH
    # 0.08 ETH * 2600 = 208 USDT
    # Gross Profit = +8 USDT on 200 USDT investment (4%!). Extremely viable.
    
    client = SimulatedExchangeClient(
        "binance", 
        obm, 
        simulated_latency_ms=0, 
        error_probability=0.0, 
        partial_fill_probability=0.0
    )
    
    store = InMemoryStateStore()
    notifier = ConsoleNotifier()
    logger = ExecutionLogger()
    sm = ExecutionStateMachine(store, notifier, logger)
    fee_calc = FeeCalculator({})
    
    config = {
        'min_profit_threshold_pct': 0.15,
        'slippage_buffer_pct': 0.0,
        'partial_fill_min_viable_pct': 50.0
    }
    
    strategy = TriangularArbitrageStrategy(client, fee_calc, obm, sm, config)
    
    triangle_def = [
        {'symbol': 'BTCUSDT', 'side': 'buy'},
        {'symbol': 'ETHBTC', 'side': 'buy'},
        {'symbol': 'ETHUSDT', 'side': 'sell'}
    ]
    
    return strategy, client, sm, store, triangle_def

@pytest.mark.asyncio
async def test_triangular_evaluate_viable(strategy_env):
    strategy, _, _, _, triangle_def = strategy_env
    
    result = await strategy.evaluate_triangle(triangle_def, 200.0)
    
    assert result['is_viable'] is True
    assert result['gross_pnl'] == 8.0 # 208 - 200

@pytest.mark.asyncio
async def test_triangular_execute_happy_path(strategy_env):
    strategy, client, sm, store, triangle_def = strategy_env
    
    result = await strategy.evaluate_triangle(triangle_def, 200.0)
    context = ExecutionContext(execution_id="tri_1", strategy="triangular")
    
    await strategy.execute_triangle(context, result['legs'])
    
    assert context.state == ExecutionState.COMPLETED
    assert len(context.data["filled_legs"]) == 3

@pytest.mark.asyncio
async def test_triangular_partial_fill_unwind(strategy_env):
    strategy, client, sm, store, triangle_def = strategy_env
    
    # Force the simulator to partial fill the first leg below the 50% limit (e.g. 10%)
    client.partial_fill_prob = 1.0
    import random
    random.seed(42) # Ensure we get a specific fill or mock it
    # We will just manually override place_order to return a tiny fill
    original_place = client.place_order
    async def mock_place_order(symbol, side, order_type, quantity, price=None):
        if symbol == 'BTCUSDT':
            return {"id": "1", "symbol": symbol, "side": side, "filled_qty": quantity * 0.1} # 10% fill
        return await original_place(symbol, side, order_type, quantity, price)
    client.place_order = mock_place_order
    
    result = await strategy.evaluate_triangle(triangle_def, 200.0)
    context = ExecutionContext(execution_id="tri_2", strategy="triangular")
    
    await strategy.execute_triangle(context, result['legs'])
    
    # State should be UNWOUND because it partial filled below the 50% threshold
    assert context.state == ExecutionState.UNWOUND
