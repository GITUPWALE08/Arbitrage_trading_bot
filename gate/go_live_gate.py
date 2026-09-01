from typing import List, Dict, Any
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from core.logger import logging

logger = logging.getLogger("GoLiveGate")
logger.setLevel(logging.INFO)

class TradeJournalMock:
    """
    Mock interface for the DB tables needed by the Gate.
    In reality, this would query the `executions` and `reconciliation_log` tables.
    """
    def __init__(self):
        self.executions: List[Dict[str, Any]] = []
        self.reconciliation_logs: List[Dict[str, Any]] = []
        self.paper_trading_start: datetime = datetime.now(timezone.utc)

class GateConfig(BaseModel):
    min_days_paper_trading: int = 14
    min_executions_per_strategy: int = 50
    max_single_trade_profit_pct: float = 50.0  # e.g., no single trade can be >50% of total profit
    max_drawdown_usd: float = 200.0
    manual_sign_off: bool = False

class GoLiveGate:
    """
    Automated pre-live checklist (Section 9).
    """
    def __init__(self, journal: TradeJournalMock, config: GateConfig):
        self.journal = journal
        self.config = config

    def _check_duration(self) -> (bool, str):
        days_active = (datetime.now(timezone.utc) - self.journal.paper_trading_start).days
        if days_active < self.config.min_days_paper_trading:
            return False, f"Paper traded for {days_active} days. Required: {self.config.min_days_paper_trading}."
        return True, "Duration check passed."

    def _check_sample_size(self, strategy: str) -> (bool, str):
        strat_execs = [e for e in self.journal.executions if e.get('strategy') == strategy and e.get('state') == 'COMPLETED']
        count = len(strat_execs)
        if count < self.config.min_executions_per_strategy:
            return False, f"Strategy {strategy} has {count} executions. Required: {self.config.min_executions_per_strategy}."
        return True, f"Sample size check passed ({count} executions)."

    def _check_profitability_and_consistency(self, strategy: str) -> (bool, str):
        strat_execs = [e for e in self.journal.executions if e.get('strategy') == strategy and e.get('state') == 'COMPLETED']
        total_profit = sum(e.get('realized_profit', 0) for e in strat_execs)
        
        if total_profit <= 0:
            return False, f"Strategy {strategy} is not net profitable (P&L: {total_profit})."
            
        max_single_profit = max(e.get('realized_profit', 0) for e in strat_execs)
        concentration = (max_single_profit / total_profit) * 100
        
        if concentration > self.config.max_single_trade_profit_pct:
            return False, f"Consistency check failed. Single trade accounts for {concentration:.1f}% of profit (Max allowed: {self.config.max_single_trade_profit_pct}%)."
            
        return True, f"Profitability check passed. Total P&L: {total_profit}, Max concentration: {concentration:.1f}%."

    def _check_drawdown(self, strategy: str) -> (bool, str):
        strat_execs = sorted(
            [e for e in self.journal.executions if e.get('strategy') == strategy and e.get('state') in ('COMPLETED', 'UNWOUND', 'STUCK')],
            key=lambda x: x.get('created_at', datetime.now(timezone.utc))
        )
        
        peak = 0.0
        current_balance = 0.0
        max_dd = 0.0
        
        for e in strat_execs:
            current_balance += e.get('realized_profit', 0)
            if current_balance > peak:
                peak = current_balance
            dd = peak - current_balance
            if dd > max_dd:
                max_dd = dd
                
        if max_dd > self.config.max_drawdown_usd:
            return False, f"Max drawdown ({max_dd}) exceeds threshold ({self.config.max_drawdown_usd})."
            
        return True, f"Drawdown check passed (Max DD: {max_dd})."

    def _check_reconciliation(self) -> (bool, str):
        critical_discrepancies = [log for log in self.journal.reconciliation_logs if log.get('severity') == 'CRITICAL']
        if critical_discrepancies:
            return False, f"Found {len(critical_discrepancies)} critical reconciliation discrepancies."
        return True, "Reconciliation check passed."

    def _check_manual_signoff(self) -> (bool, str):
        if not self.config.manual_sign_off:
            return False, "Manual sign-off flag is False."
        return True, "Manual sign-off provided."

    def evaluate(self, strategy: str) -> (bool, str):
        """
        Runs all checks for a given strategy and returns (passed, full_report).
        """
        report_lines = [f"--- Go-Live Gate Evaluation for {strategy} ---"]
        
        checks = [
            self._check_duration,
            lambda: self._check_sample_size(strategy),
            lambda: self._check_profitability_and_consistency(strategy),
            lambda: self._check_drawdown(strategy),
            self._check_reconciliation,
            self._check_manual_signoff
        ]
        
        all_passed = True
        
        for check in checks:
            passed, msg = check()
            status = "PASS" if passed else "FAIL"
            report_lines.append(f"[{status}] {msg}")
            if not passed:
                all_passed = False
                
        if all_passed:
            report_lines.append("RESULT: PASSED. Bot is cleared for live trading.")
        else:
            report_lines.append("RESULT: FAILED. Bot is locked to paper trading.")
            
        return all_passed, "\n".join(report_lines)
