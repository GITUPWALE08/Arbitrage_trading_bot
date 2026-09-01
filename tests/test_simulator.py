import pytest
from paper_trading.simulator import SimulatedExchangeClient
from core.orderbook_manager import OrderBookManager, OrderBook
import asyncio

@pytest.fixture
def orderbook_manager():
    obm = OrderBookManager()
    book = OrderBook(
        symbol="BTCUSDT",
        exchange="binance",
        bids=[(50000.0, 1.0), (49900.0, 5.0)],
        asks=[(50100.0, 0.5), (50200.0, 2.0)]
    )
    obm.books[("binance", "BTCUSDT")] = book
    return obm

@pytest.mark.asyncio
async def test_simulator_perfect_fill(orderbook_manager):
    # No error, no partial fill, low latency
    client = SimulatedExchangeClient(
        "binance", 
        orderbook_manager, 
        simulated_latency_ms=0, 
        error_probability=0.0, 
        partial_fill_probability=0.0
    )
    
    order = await client.place_order("BTCUSDT", "buy", "market", 0.5)
    
    assert order["filled_qty"] == 0.5
    assert order["status"] == "closed"
    assert order["average_price"] == 50100.0
    
    # Check balances updated correctly
    balances = await client.get_balances()
    assert balances["BTC"] == 10.5
    assert balances["USDT"] == 100000.0 - (0.5 * 50100.0)

@pytest.mark.asyncio
async def test_simulator_depth_walk_slippage(orderbook_manager):
    client = SimulatedExchangeClient(
        "binance", 
        orderbook_manager, 
        simulated_latency_ms=0, 
        error_probability=0.0, 
        partial_fill_probability=0.0
    )
    
    # Buying 1.5 BTC: 0.5 @ 50100, 1.0 @ 50200 = avg price 50166.66
    order = await client.place_order("BTCUSDT", "buy", "market", 1.5)
    
    assert order["filled_qty"] == 1.5
    assert order["average_price"] > 50100.0  # Demonstrates slippage beyond top of book
    assert order["simulated_slippage_pct"] > 0

@pytest.mark.asyncio
async def test_simulator_api_error(orderbook_manager):
    # Force error
    client = SimulatedExchangeClient(
        "binance", 
        orderbook_manager, 
        simulated_latency_ms=0, 
        error_probability=1.0, 
        partial_fill_probability=0.0
    )
    
    with pytest.raises(ConnectionError, match="Simulated Exchange API Error"):
        await client.place_order("BTCUSDT", "buy", "market", 1.0)
