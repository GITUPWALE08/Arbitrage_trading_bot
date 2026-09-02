import pytest
from core.reconciliation import ReconciliationManager
from core.state_store import InMemoryStateStore
from core.notifier import ConsoleNotifier
from core.exchange_client import ExchangeClient

class DummyExchangeClient(ExchangeClient):
    def __init__(self, balances: dict):
        self.exchange_name = "test_exchange"
        self._balances = balances
    async def get_balances(self) -> dict:
        return self._balances
    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float = None) -> dict: return {}
    async def get_order_status(self, order_id: str, symbol: str) -> dict: return {}
    async def get_historical_funding_rates(self, symbol: str, days: int) -> list: return []
    async def get_mark_price(self, symbol: str) -> float: return 0.0
    async def get_margin_ratio(self, symbol: str) -> float: return 0.1

@pytest.fixture
def store():
    return InMemoryStateStore()

@pytest.fixture
def notifier():
    return ConsoleNotifier()

@pytest.mark.asyncio
async def test_reconciliation_happy_path(store, notifier, capsys):
    client = DummyExchangeClient({"BTC": 1.0, "USDT": 50000.0})
    await store.set_expected_balances("binance", {"BTC": 1.0, "USDT": 50000.0})
    
    manager = ReconciliationManager(
        state_store=store,
        exchange_clients={"binance": client},
        notifier=notifier
    )
    
    await manager.reconcile_exchange("binance")
    
    # Check that no alerts were sent
    captured = capsys.readouterr()
    assert "HIGH PRIORITY ALERT" not in captured.out
    
    assert not hasattr(store, '_reconciliation_logs') or len(store._reconciliation_logs) == 0

@pytest.mark.asyncio
async def test_reconciliation_dust_ignored(store, notifier, capsys):
    # 0.0000001 diff is within 1e-6 dust tolerance
    client = DummyExchangeClient({"BTC": 1.0000001})
    await store.set_expected_balances("binance", {"BTC": 1.0})
    
    manager = ReconciliationManager(
        state_store=store,
        exchange_clients={"binance": client},
        notifier=notifier,
        dust_tolerance=1e-6
    )
    
    await manager.reconcile_exchange("binance")
    
    captured = capsys.readouterr()
    assert "HIGH PRIORITY ALERT" not in captured.out

@pytest.mark.asyncio
async def test_reconciliation_critical_discrepancy(store, notifier, capsys):
    # 0.5 diff is > 0.01 halt threshold
    client = DummyExchangeClient({"BTC": 0.5})
    await store.set_expected_balances("binance", {"BTC": 1.0})
    
    manager = ReconciliationManager(
        state_store=store,
        exchange_clients={"binance": client},
        notifier=notifier,
        halt_threshold=0.01
    )
    
    await manager.reconcile_exchange("binance")
    
    # Check that an alert was sent
    captured = capsys.readouterr()
    assert "HIGH PRIORITY ALERT" in captured.out
    assert "CRITICAL" in captured.out
    
    # Check log was saved
    assert len(store._reconciliation_logs) == 1
    log = store._reconciliation_logs[0]
    assert log["severity"] == "CRITICAL"
    assert "BTC" in log["discrepancies"]
