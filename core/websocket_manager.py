import asyncio
import time
from typing import Dict, Callable, Any
from core.logger import logging

logger = logging.getLogger("WebSocketManager")
logger.setLevel(logging.INFO)

class WebSocketConnectionManager:
    """
    Section 12: Stale-Data Protection (WebSocket Heartbeats).
    Manages connections and enforces ping-pong monitoring.
    """
    def __init__(self, heartbeat_interval: float = 10.0, stale_timeout: float = 30.0):
        self.heartbeat_interval = heartbeat_interval
        self.stale_timeout = stale_timeout
        self.connections: Dict[str, float] = {}  # exchange -> last_heartbeat_timestamp
        
    async def monitor_heartbeats(self):
        """
        Continuously checks if any exchange hasn't responded to heartbeats.
        """
        while True:
            now = time.time()
            for exchange, last_ts in list(self.connections.items()):
                if now - last_ts > self.stale_timeout:
                    logger.error(f"WebSocket connection to {exchange} is STALE (no heartbeat for > {self.stale_timeout}s). Triggering reconnect.")
                    await self._reconnect(exchange)
            await asyncio.sleep(self.heartbeat_interval)
            
    async def record_heartbeat(self, exchange: str):
        """
        Called when a ping/pong or message is received.
        """
        self.connections[exchange] = time.time()
        
    async def _reconnect(self, exchange: str):
        """
        Mock reconnect logic. In production this would interface with ccxt.pro
        """
        logger.info(f"Reconnecting to {exchange} WebSocket...")
        self.connections[exchange] = time.time() # reset
