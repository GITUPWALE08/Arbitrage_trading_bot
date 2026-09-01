import pytest
from core.latency_monitor import LatencyMonitor

def test_latency_monitor():
    lm = LatencyMonitor(window_size=5)
    
    # Default before data
    assert lm.get_p50_latency("binance") == 100.0
    
    # Record some latencies
    latencies = [10.0, 20.0, 30.0, 40.0, 100.0]
    for lat in latencies:
        lm.record_latency("binance", lat)
        
    p50 = lm.get_p50_latency("binance")
    assert p50 == 30.0
    
    p95 = lm.get_p95_latency("binance")
    assert p95 == 100.0
    
    # Check window size rolling
    lm.record_latency("binance", 200.0)
    # Deque should now be [20, 30, 40, 100, 200]
    assert lm.get_p50_latency("binance") == 40.0
    
    # Decay estimate check
    # p50 = 40.0 -> (40 / 10) * 0.001 = 0.004
    decay = lm.get_latency_decay_estimate_pct("binance")
    assert decay == 0.004
