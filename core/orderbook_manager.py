import time
from typing import List, Tuple, Dict, Optional
from pydantic import BaseModel, Field

class OrderBook(BaseModel):
    symbol: str
    exchange: str
    bids: List[Tuple[float, float]]  # [(price, qty)]
    asks: List[Tuple[float, float]]
    timestamp: float = Field(default_factory=time.time)

class OrderBookManager:
    def __init__(self, fast_store: 'FastStateStore' = None, stale_threshold_sec: float = 0.5):
        # We still keep in-memory for synchronous fallback where needed, but write to Redis
        self.books: Dict[Tuple[str, str], OrderBook] = {}
        self.fast_store = fast_store
        self.stale_threshold_sec = stale_threshold_sec
        
    async def update_book(self, exchange: str, symbol: str, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]], timestamp: float = None):
        ts = timestamp or time.time()
        book = OrderBook(
            symbol=symbol,
            exchange=exchange,
            bids=bids,
            asks=asks,
            timestamp=ts
        )
        self.books[(exchange, symbol)] = book
        if self.fast_store:
            await self.fast_store.set_orderbook(exchange, symbol, book.model_dump())
        
    async def get_book(self, exchange: str, symbol: str) -> Optional[OrderBook]:
        book = None
        if self.fast_store:
            data = await self.fast_store.get_orderbook(exchange, symbol)
            if data:
                book = OrderBook(**data)
        
        if not book:
            book = self.books.get((exchange, symbol))
            
        if not book:
            return None
            
        age = time.time() - book.timestamp
        if age > self.stale_threshold_sec:
            # Stale data! Return None so the strategy won't act on it.
            return None
            
        return book

