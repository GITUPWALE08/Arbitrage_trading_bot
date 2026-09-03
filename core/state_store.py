from abc import ABC, abstractmethod
from typing import Optional, List
from core.execution_engine import ExecutionContext

class StateStore(ABC):
    """
    Abstract interface for persisting execution state.
    """
    
    @abstractmethod
    async def save_execution_state(self, context: ExecutionContext):
        """
        Save the execution state to persistent storage.
        Must complete synchronously (awaitable) before the next action executes.
        """
        pass

    @abstractmethod
    async def get_execution_state(self, execution_id: str) -> Optional[ExecutionContext]:
        """
        Retrieve a specific execution context by ID.
        """
        pass

    @abstractmethod
    async def get_active_executions(self) -> List[ExecutionContext]:
        """
        Retrieve all execution contexts that are not in a terminal state.
        Useful for crash recovery (Section 2.3).
        """
        pass

    @abstractmethod
    async def get_expected_balances(self, exchange: str) -> dict:
        """
        Calculates the expected balances for an exchange based on past executions.
        Returns a dict of {asset: amount}.
        """
        pass
        
    @abstractmethod
    async def save_reconciliation_log(self, exchange: str, expected: dict, actual: dict, discrepancies: dict, severity: str):
        """
        Writes discrepancies to the reconciliation_log table.
        """
        pass

    @abstractmethod
    async def get_kill_switch(self, scope: str, scope_value: str = None) -> dict:
        pass
        
    @abstractmethod
    async def set_kill_switch(self, scope: str, scope_value: str, is_tripped: bool, tripped_by: str, reason: str):
        pass

    @abstractmethod
    async def save_execution_leg(self, leg_data: dict):
        pass

    @abstractmethod
    async def save_opportunity(self, opp_data: dict):
        pass

    @abstractmethod
    async def save_balances_snapshot(self, exchange: str, asset: str, balance: float, source: str):
        pass

    @abstractmethod
    async def save_margin_monitoring(self, position_id: str, exchange: str, symbol: str, margin_ratio: float, liquidation_price: float = None):
        pass

    @abstractmethod
    async def save_funding_rate(self, exchange: str, symbol: str, rate: float, annualized_pct: float):
        pass


    @abstractmethod
    async def get_pnl(self, mode: str = None) -> float:
        pass
        
    @abstractmethod
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        pass


    @abstractmethod
    async def get_latest_balances(self) -> dict: pass
    
    @abstractmethod
    async def get_recent_opportunities(self) -> list: pass
    
    @abstractmethod
    async def get_recent_executions(self) -> list: pass
    
    @abstractmethod
    async def get_all_kill_switches(self) -> list: pass
class InMemoryStateStore(StateStore):
    """
    In-memory state store for testing and paper trading before DB is wired up.
    """
    def __init__(self):
        self._store = {}

    async def save_execution_state(self, context: ExecutionContext):
        self._store[context.execution_id] = context.model_copy(deep=True)

    async def get_execution_state(self, execution_id: str) -> Optional[ExecutionContext]:
        context = self._store.get(execution_id)
        return context.model_copy(deep=True) if context else None

    async def get_active_executions(self) -> List[ExecutionContext]:
        from core.execution_engine import ExecutionState
        terminal_states = {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.UNWOUND}
        
        active = []
        for context in self._store.values():
            if context.state not in terminal_states:
                active.append(context.model_copy(deep=True))
        return active

    async def get_expected_balances(self, exchange: str) -> dict:
        # For testing, we'll store expected balances in a simple dict
        if not hasattr(self, '_expected_balances'):
            self._expected_balances = {}
        return self._expected_balances.get(exchange, {})
        
    async def set_expected_balances(self, exchange: str, balances: dict):
        if not hasattr(self, '_expected_balances'):
            self._expected_balances = {}
        self._expected_balances[exchange] = balances

    async def save_reconciliation_log(self, exchange: str, expected: dict, actual: dict, discrepancies: dict, severity: str):
        if not hasattr(self, '_reconciliation_logs'):
            self._reconciliation_logs = []
        self._reconciliation_logs.append({
            "exchange": exchange,
            "expected": expected,
            "actual": actual,
            "discrepancies": discrepancies,
            "severity": severity
        })

    async def get_kill_switch(self, scope: str, scope_value: str = None) -> dict:
        if not hasattr(self, '_kill_switches'):
            self._kill_switches = {}
        key = f"{scope}:{scope_value}" if scope_value else scope
        return self._kill_switches.get(key)
        
    async def set_kill_switch(self, scope: str, scope_value: str, is_tripped: bool, tripped_by: str, reason: str):
        if not hasattr(self, '_kill_switches'):
            self._kill_switches = {}
        key = f"{scope}:{scope_value}" if scope_value else scope
        self._kill_switches[key] = {
            "scope": scope,
            "scope_value": scope_value,
            "is_tripped": is_tripped,
            "tripped_by": tripped_by,
            "reason": reason
        }

    async def save_execution_leg(self, leg_data: dict): pass
    async def save_opportunity(self, opp_data: dict): pass
    async def save_balances_snapshot(self, exchange: str, asset: str, balance: float, source: str): pass
    async def save_margin_monitoring(self, position_id: str, exchange: str, symbol: str, margin_ratio: float, liquidation_price: float = None): pass
    async def save_funding_rate(self, exchange: str, symbol: str, rate: float, annualized_pct: float): pass


    
    async def get_pnl(self, mode: str = None) -> float:
        return 0.0
        
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        return {}

    async def get_latest_balances(self) -> dict: return {}
    async def get_recent_opportunities(self) -> list: return []
    async def get_recent_executions(self) -> list: return []
    async def get_all_kill_switches(self) -> list: return []
