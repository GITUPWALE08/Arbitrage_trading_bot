import pytest
import time
from core.orderbook_manager import OrderBookManager

@pytest.mark.asyncio
async def test_orderbook_staleness():
    obm = OrderBookManager(stale_threshold_sec=0.5)
    
    # 1. Fresh data
    await obm.update_book("binance", "BTCUSDT", [(50000.0, 1.0)], [(50100.0, 1.0)])
    book = await obm.get_book("binance", "BTCUSDT")
    assert book is not None
    assert book.bids[0][0] == 50000.0
    
    # 2. Simulate time passing (manual injection of old timestamp)
    old_time = time.time() - 1.0 # 1 second ago (stale > 0.5s)
    await obm.update_book("binance", "ETHUSDT", [(2000.0, 1.0)], [(2010.0, 1.0)], timestamp=old_time)
    
    stale_book = await obm.get_book("binance", "ETHUSDT")
    assert stale_book is None  # Should be rejected as stale
