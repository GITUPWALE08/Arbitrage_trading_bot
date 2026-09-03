import pytest
from core.fee_calculator import FeeCalculator
from core.orderbook_manager import OrderBook

@pytest.mark.asyncio
async def test_fee_calculator_happy_path():
    calc = FeeCalculator({})
    
    # Sell side book (Asks) - if we buy, we match against asks
    binance_book = OrderBook(
        symbol="BTCUSDT",
        exchange="binance",
        bids=[],
        asks=[(50000.0, 0.5), (50100.0, 1.0)] # 0.5 at 50000, 1.0 at 50100
    )
    
    # Buy side book (Bids) - if we sell, we match against bids
    bybit_book = OrderBook(
        symbol="BTCUSDT",
        exchange="bybit",
        bids=[(50500.0, 0.8), (50400.0, 1.0)], # 0.8 at 50500, 1.0 at 50400
        asks=[]
    )
    
    # We want to buy 1.0 BTC on Binance and sell 1.0 BTC on Kraken
    # Binance Buy (matched against asks): 
    # 0.5 at 50,000 = $25,000
    # 0.5 at 50,100 = $25,050
    # Total cost = $50,050 (avg 50,050)
    
    # Kraken Sell (matched against bids):
    # 0.8 at 50,500 = $40,400
    # 0.2 at 50,400 = $10,080
    # Total rev = $50,480 (avg 50,480)
    
    # Gross profit: 50,480 - 50,050 = $430
    
    # Notional traded = 50,050 + 50,480 = $100,530
    # Fees (0.1% per leg) = $100.53
    # Slippage (0.05% of notional) = $50.265
    # Latency (0.01% of notional) = $10.053
    # Cross exchange = $10
    
    # Total costs = 100.53 + 50.265 + 10.053 + 10 = $170.848
    # Net profit = 430 - 170.848 = 259.152
    
    legs = [
        {'exchange': 'binance', 'symbol': 'BTCUSDT', 'side': 'buy', 'size': 1.0, 'order_book': binance_book},
        {'exchange': 'bybit', 'symbol': 'BTCUSDT', 'side': 'sell', 'size': 1.0, 'order_book': bybit_book},
    ]
    
    result = await calc.calculate_net_profit(
        strategy="cross_exchange",
        legs=legs,
        slippage_buffer_pct=0.05,
        latency_decay_estimate_pct=0.01,
        min_profit_threshold=50.0,
        cross_exchange_withdrawal_fee=10.0
    )
    
    assert result["is_viable"] is True
    assert result["gross_pnl"] == 430.0
    assert abs(result["total_fees"] - 110.53) < 0.001
    assert abs(result["slippage_cost"] - 50.265) < 0.001
    assert abs(result["latency_cost"] - 10.053) < 0.001
    assert abs(result["net_profit"] - 259.152) < 0.001

@pytest.mark.asyncio
async def test_fee_calculator_insufficient_depth():
    calc = FeeCalculator({})
    book = OrderBook(
        symbol="BTCUSDT",
        exchange="binance",
        bids=[],
        asks=[(50000.0, 0.5)] # Only 0.5 available
    )
    
    with pytest.raises(ValueError, match="Insufficient order book depth"):
        calc.walk_order_book(book, 'buy', 1.0)
