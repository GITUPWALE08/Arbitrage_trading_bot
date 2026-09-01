import json
from typing import Dict, Optional, Any
from core.logger import logging
import redis.asyncio as redis

logger = logging.getLogger("RedisState")
logger.setLevel(logging.INFO)

class FastStateStore:
    """
    Section 11: Redis / Fast State
    Used for sub-millisecond reads on the hot path (live order book cache, current positions, distributed locks).
    """
    def __init__(self, redis_url: str = "redis://localhost"):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
        
    async def connect(self):
        try:
            self.client = redis.from_url(self.redis_url, decode_responses=True)
            await self.client.ping()
            logger.info("Connected to Redis Fast State.")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis at {self.redis_url}. Using in-memory fallback. Error: {e}")
            self.client = None
            self._fallback_cache = {}
            self._fallback_locks = set()
            
    async def set_orderbook(self, exchange: str, symbol: str, data: dict):
        key = f"ob:{exchange}:{symbol}"
        if self.client:
            await self.client.set(key, json.dumps(data))
        else:
            self._fallback_cache[key] = data
            
    async def get_orderbook(self, exchange: str, symbol: str) -> Optional[dict]:
        key = f"ob:{exchange}:{symbol}"
        if self.client:
            val = await self.client.get(key)
            return json.loads(val) if val else None
        else:
            return self._fallback_cache.get(key)
            
    async def acquire_lock(self, lock_name: str, timeout_sec: int = 5) -> bool:
        """
        Distributed lock to prevent concurrent evaluation of the same opportunity.
        """
        key = f"lock:{lock_name}"
        if self.client:
            return await self.client.set(key, "1", nx=True, ex=timeout_sec)
        else:
            if key in self._fallback_locks:
                return False
            self._fallback_locks.add(key)
            return True
            
    async def release_lock(self, lock_name: str):
        key = f"lock:{lock_name}"
        if self.client:
            await self.client.delete(key)
        else:
            self._fallback_locks.discard(key)
