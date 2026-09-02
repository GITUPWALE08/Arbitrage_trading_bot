import asyncio
import random
import uuid
import time
from typing import Dict, Any, Optional
from core.exchange_client import ExchangeClient
from core.orderbook_manager import OrderBookManager
from core.logger import logging

logger = logging.getLogger("PaperSimulator")
logger.setLevel(logging.INFO)

class SimulatedExchangeClient(ExchangeClient):
    """
    Realistic paper trading simulator per Section 2.5.
    Wraps the standard ExchangeClient interface to drop seamlessly into the execution engine.
    """
    def __init__(
        self, 
        exchange_name: str, 
        orderbook_manager: OrderBookManager,
        simulated_latency_ms: int = 150,
        error_probability: float = 0.01,
        partial_fill_probability: float = 0.1,
        initial_balances: Optional[Dict[str, float]] = None
    ):
        self.exchange_name = exchange_name
        self.orderbook_manager = orderbook_manager
        self.latency_ms = simulated_latency_ms
        self.error_prob = error_probability
        self.partial_fill_prob = partial_fill_probability
        self.balances = initial_balances or {"USDT": 100000.0, "BTC": 10.0, "ETH": 100.0}
        self.orders = {}

    async def get_balances(self) -> dict:
        # Simulate network latency
        await asyncio.sleep(self.latency_ms / 1000.0)
        return self.balances

    async def get_order_status(self, order_id: str, symbol: str) -> dict:
        await asyncio.sleep(self.latency_ms / 1000.0)
        if order_id not in self.orders:
            raise ValueError("Order not found")
        return self.orders[order_id]

    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float = None) -> dict:
        """
        Simulates order placement with realistic depth consumption, latency, and partial fills.
        """
        start_time = time.time()
        
        # 1. Simulate network/decision latency delay (Section 2.5)
        await asyncio.sleep(self.latency_ms / 1000.0)
        
        # 2. Occasional random API failures (Section 2.5)
        if random.random() < self.error_prob:
            raise ConnectionError(f"Simulated Exchange API Error on {self.exchange_name}")

        book = await self.orderbook_manager.get_book(self.exchange_name, symbol)
        if not book:
            raise ValueError(f"No orderbook found for {symbol} on {self.exchange_name}")

        order_id = str(uuid.uuid4())
        
        # Default naive slippage baseline (flat top of book)
        top_ask = book.asks[0][0] if book.asks else 0
        top_bid = book.bids[0][0] if book.bids else 0
        naive_price = top_ask if side == "buy" else top_bid
        
        # 3. Simulate probabilistic partial fills (Section 2.5)
        actual_qty_to_fill = quantity
        if random.random() < self.partial_fill_prob:
            # Simulate picking a random filled percentage between 10% and 90%
            fill_pct = random.uniform(0.1, 0.9)
            actual_qty_to_fill = quantity * fill_pct

        # 4. Walk the book at simulation time (post-latency)
        levels = book.asks if side == 'buy' else book.bids
        remaining_qty = actual_qty_to_fill
        total_cost = 0.0
        
        for p, q in levels:
            if remaining_qty <= 0:
                break
            # Enforce limit price if applicable
            if order_type == 'limit':
                if side == 'buy' and p > price:
                    break
                if side == 'sell' and p < price:
                    break
            
            fill = min(remaining_qty, q)
            total_cost += fill * p
            remaining_qty -= fill
            
        filled_qty = actual_qty_to_fill - remaining_qty
        
        status = "closed" if filled_qty == quantity else "open"
        if remaining_qty > 0 and order_type == 'market':
            status = "closed" # Rest is cancelled in market order if no depth
        
        if filled_qty > 0:
            avg_price = total_cost / filled_qty
        else:
            avg_price = 0.0

        # Naive vs actual slippage tracking
        simulated_slippage = abs(avg_price - naive_price) / naive_price if naive_price and avg_price else 0

        # Update simulated balances
        base_asset = symbol.replace("USDT", "") # Simple split for MVP
        quote_asset = "USDT"
        
        if side == "buy":
            self.balances[base_asset] = self.balances.get(base_asset, 0) + filled_qty
            self.balances[quote_asset] = self.balances.get(quote_asset, 0) - total_cost
        else:
            self.balances[base_asset] = self.balances.get(base_asset, 0) - filled_qty
            self.balances[quote_asset] = self.balances.get(quote_asset, 0) + total_cost

        order_record = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "status": status,
            "intended_qty": quantity,
            "filled_qty": filled_qty,
            "average_price": avg_price,
            "naive_price": naive_price,
            "simulated_slippage_pct": simulated_slippage * 100.0,
            "latency_ms_actual": (time.time() - start_time) * 1000
        }
        
        self.orders[order_id] = order_record
        logger.info(f"Simulated order {order_id} filled. Qty: {filled_qty}/{quantity}, Avg: {avg_price}, Slipage: {simulated_slippage*100:.3f}%")
        
        return order_record
        
    async def get_historical_funding_rates(self, symbol: str, days_back: int) -> list:
        # Mock empty/flat for now since we aren't testing funding on simulator yet
        return []
        
    async def get_mark_price(self, symbol: str) -> float:
        # Default to orderbook mid-price if available
        book = await self.orderbook_manager.get_book(self.exchange_name, symbol)
        if book and book.asks and book.bids:
            return (book.asks[0][0] + book.bids[0][0]) / 2.0
        return 0.0

    async def get_margin_ratio(self, symbol: str) -> float:
        return 0.1 # Mocked healthy margin ratio
