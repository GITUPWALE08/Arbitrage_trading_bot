import pytest
import time
from strategies.funding_rate import FundingRateStrategy
from core.exchange_client import ExchangeClient
from core.fee_calculator import FeeCalculator

class MockExchangeForFunding(ExchangeClient):
    def __init__(self, history, spot_price, perp_price):
        self.history = history
        self.spot_price = spot_price
        self.perp_price = perp_price
        
    async def get_historical_funding_rates(self, symbol, days):
        return self.history
        
    async def get_mark_price(self, symbol):
        if "PERP" in symbol:
            return self.perp_price
        return self.spot_price
        
    async def get_margin_ratio(self, symbol: str) -> float:
        return 0.1
    async def get_balances(self): return {}
    async def get_order_status(self, oid, sym): return {}
    async def place_order(self, sym, side, typ, qty, px=None): return {}

@pytest.fixture
def config():
    return {
        'funding_history_window_days': 10,
        'min_trailing_annualized_funding_pct': 10.0,
        'max_negative_flips_in_window': 2,
        'max_basis_pct': 0.5,
        'min_holding_period_hr': 24
    }

@pytest.mark.asyncio
async def test_funding_strategy_entry_happy_path(config):
    # Rates that average to ~10.95% annualized (0.01% per 8h * 3 * 365 = 10.95%)
    history = [{'timestamp': 0, 'rate': 0.0001}] * 30
    
    client = MockExchangeForFunding(history, spot_price=50000, perp_price=50100) # 0.2% basis
    strategy = FundingRateStrategy(client, FeeCalculator({}), config)
    
    result = await strategy.evaluate_entry("BTCUSDT", "BTCUSDT-PERP")
    assert result["enter"] is True
    assert result["avg_annualized_pct"] > 10.0
    assert result["basis_pct"] == 0.2

@pytest.mark.asyncio
async def test_funding_strategy_entry_too_many_flips(config):
    history = [{'timestamp': 0, 'rate': 0.0002}] * 27 + [{'timestamp': 0, 'rate': -0.0001}] * 3 # 3 flips
    
    client = MockExchangeForFunding(history, spot_price=50000, perp_price=50100)
    strategy = FundingRateStrategy(client, FeeCalculator({}), config)
    
    result = await strategy.evaluate_entry("BTCUSDT", "BTCUSDT-PERP")
    assert result["enter"] is False
    assert "Too many negative flips" in result["reason"]

@pytest.mark.asyncio
async def test_funding_strategy_exit_min_holding(config):
    history = [{'timestamp': 0, 'rate': -0.0001}] * 30 # Bad funding, should exit
    client = MockExchangeForFunding(history, spot_price=50000, perp_price=50100)
    strategy = FundingRateStrategy(client, FeeCalculator({}), config)
    
    # Check 1 hour held (below 24h min)
    entry_time = time.time() - 3600
    result = await strategy.evaluate_exit("BTCUSDT", "BTCUSDT-PERP", entry_time)
    
    assert result["exit"] is False
    assert "Minimum holding period not reached" in result["reason"]

    # Check 25 hours held
    entry_time = time.time() - (25 * 3600)
    result = await strategy.evaluate_exit("BTCUSDT", "BTCUSDT-PERP", entry_time)
    
    assert result["exit"] is True
    assert "Funding rate deteriorated" in result["reason"]
