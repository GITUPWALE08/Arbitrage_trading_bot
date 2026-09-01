import pytest
from datetime import datetime, timedelta, timezone
from gate.go_live_gate import GoLiveGate, TradeJournalMock, GateConfig

@pytest.fixture
def journal():
    j = TradeJournalMock()
    # Mock duration
    j.paper_trading_start = datetime.now(timezone.utc) - timedelta(days=15)
    
    # Mock executions
    for i in range(50):
        profit = 5.0
        # Make one trade highly profitable to test concentration (but not > 50% if total is 250)
        if i == 0:
            profit = 50.0 
        
        j.executions.append({
            'strategy': 'funding_rate',
            'state': 'COMPLETED',
            'realized_profit': profit,
            'created_at': datetime.now(timezone.utc)
        })
    return j

def test_go_live_gate_happy_path(journal):
    config = GateConfig(manual_sign_off=True)
    gate = GoLiveGate(journal, config)
    
    passed, report = gate.evaluate('funding_rate')
    assert passed is True
    assert "PASSED. Bot is cleared for live trading" in report

def test_go_live_gate_fails_duration(journal):
    # Less than 14 days
    journal.paper_trading_start = datetime.now(timezone.utc) - timedelta(days=5)
    
    config = GateConfig(manual_sign_off=True)
    gate = GoLiveGate(journal, config)
    
    passed, report = gate.evaluate('funding_rate')
    assert passed is False
    assert "[FAIL] Paper traded for 5 days" in report

def test_go_live_gate_fails_consistency(journal):
    # Add a massive single trade that accounts for >50% of profit
    journal.executions.append({
        'strategy': 'funding_rate',
        'state': 'COMPLETED',
        'realized_profit': 1000.0,
        'created_at': datetime.now(timezone.utc)
    })
    
    config = GateConfig(manual_sign_off=True)
    gate = GoLiveGate(journal, config)
    
    passed, report = gate.evaluate('funding_rate')
    assert passed is False
    assert "Consistency check failed" in report

def test_go_live_gate_fails_manual_signoff(journal):
    config = GateConfig(manual_sign_off=False)
    gate = GoLiveGate(journal, config)
    
    passed, report = gate.evaluate('funding_rate')
    assert passed is False
    assert "Manual sign-off flag is False" in report

def test_go_live_gate_fails_reconciliation(journal):
    journal.reconciliation_logs.append({'severity': 'CRITICAL'})
    
    config = GateConfig(manual_sign_off=True)
    gate = GoLiveGate(journal, config)
    
    passed, report = gate.evaluate('funding_rate')
    assert passed is False
    assert "critical reconciliation discrepancies" in report
