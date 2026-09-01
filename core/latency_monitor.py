import time
import collections
import statistics
from typing import Dict
from core.logger import logging

logger = logging.getLogger("LatencyMonitor")
logger.setLevel(logging.INFO)

class LatencyMonitor:
    """
    Section 2.6: Latency Monitor
    Tracks decision-to-fill and network latency to feed the fee calculator's decay model.
    """
    def __init__(self, window_size: int = 50):
        self.window_size = window_size
        self.latencies_ms: Dict[str, collections.deque] = {}
        self.default_latency = 100.0  # Fallback 100ms
        
    def record_latency(self, exchange: str, latency_ms: float):
        """
        Records a new latency datapoint for an exchange.
        """
        if exchange not in self.latencies_ms:
            self.latencies_ms[exchange] = collections.deque(maxlen=self.window_size)
            
        self.latencies_ms[exchange].append(latency_ms)
        
    def get_p50_latency(self, exchange: str) -> float:
        """
        Returns the median (p50) latency for the exchange in milliseconds.
        """
        if exchange not in self.latencies_ms or len(self.latencies_ms[exchange]) == 0:
            return self.default_latency
            
        return statistics.median(self.latencies_ms[exchange])
        
    def get_p95_latency(self, exchange: str) -> float:
        """
        Returns the 95th percentile latency for the exchange in milliseconds.
        """
        if exchange not in self.latencies_ms or len(self.latencies_ms[exchange]) == 0:
            return self.default_latency
            
        data = sorted(self.latencies_ms[exchange])
        idx = int(len(data) * 0.95)
        if idx >= len(data):
            idx = len(data) - 1
            
        return data[idx]

    def get_latency_decay_estimate_pct(self, exchange: str) -> float:
        """
        Converts the current p50 latency into a conservative decay estimate percentage.
        E.g., if latency is high, we estimate more slippage.
        This provides the dynamic parameter for the FeeCalculator.
        """
        p50 = self.get_p50_latency(exchange)
        # Simple heuristic model: 0.001% decay per 10ms of latency
        decay_pct = (p50 / 10.0) * 0.001
        
        # Cap it to a reasonable maximum
        if decay_pct > 0.1:
            decay_pct = 0.1
            
        return decay_pct
