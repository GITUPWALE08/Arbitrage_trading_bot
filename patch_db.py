import re

with open("core/database.py", "r") as f:
    content = f.read()

# Update __init__
content = re.sub(
    r'def __init__\(self, db_url: str\):',
    r'def __init__(self, db_url: str, active_mode: str = "simulated"):\n        self.active_mode = active_mode',
    content
)

# Update writes
writes_to_update = [
    (r'(record = ExecutionRecord\([^)]+)', r'\1, mode=self.active_mode)'),
    (r'(log = ReconciliationLog\([^)]+)', r'\1, mode=self.active_mode)'),
    (r'(record = ExecutionLeg\(\*\*leg_data)', r'\1, mode=self.active_mode)'),
    (r'(record = OpportunityRecord\(\*\*opp_data)', r'\1, mode=self.active_mode)'),
    (r'(record = BalancesSnapshot\([^)]+)', r'\1, mode=self.active_mode)'),
    (r'(record = MarginMonitoring\([^)]+)', r'\1, mode=self.active_mode)'),
    (r'(record = SystemEvent\([^)]+)', r'\1, mode=self.active_mode)')
]

for w, repl in writes_to_update:
    if "ExecutionLeg" in w or "OpportunityRecord" in w:
        content = re.sub(w, repl, content)
    else:
        content = re.sub(w + r'\)', repl, content)

# Update queries
query_updates = [
    (r'def get_stuck_positions\(self\) -> List\[dict\]:', r'def get_stuck_positions(self, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode', r'stmt = select\(ExecutionRecord\).where\(ExecutionRecord.state == ExecutionState.STUCK.name\)', r'stmt = select(ExecutionRecord).where(ExecutionRecord.state == ExecutionState.STUCK.name, ExecutionRecord.mode == mode)'),
    
    (r'def get_recent_opportunities\(self, limit: int = 5\) -> List\[dict\]:', r'def get_recent_opportunities(self, limit: int = 5, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode', r'stmt = select\(OpportunityRecord\).order_by\(OpportunityRecord.detected_at.desc\(\)\).limit\(limit\)', r'stmt = select(OpportunityRecord).where(OpportunityRecord.mode == mode).order_by(OpportunityRecord.detected_at.desc()).limit(limit)'),
    
    (r'def get_recent_executions\(self, limit: int = 5\) -> List\[dict\]:', r'def get_recent_executions(self, limit: int = 5, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode', r'stmt = select\(ExecutionRecord\).order_by\(ExecutionRecord.created_at.desc\(\)\).limit\(limit\)', r'stmt = select(ExecutionRecord).where(ExecutionRecord.mode == mode).order_by(ExecutionRecord.created_at.desc()).limit(limit)'),
    
    (r'def get_latest_balances\(self\) -> dict:', r'def get_latest_balances(self, mode: str = None) -> dict:\n        mode = mode or self.active_mode', r'stmt = select\(BalancesSnapshot\).order_by\(BalancesSnapshot.snapshot_at.desc\(\)\).limit\(100\)', r'stmt = select(BalancesSnapshot).where(BalancesSnapshot.mode == mode).order_by(BalancesSnapshot.snapshot_at.desc()).limit(100)'),
    
    (r'def get_pnl\(self\) -> float:', r'def get_pnl(self, mode: str = None) -> float:\n        mode = mode or self.active_mode', r'stmt = select\(ExecutionRecord\).where\(ExecutionRecord.state == ExecutionState.COMPLETED.name\)', r'stmt = select(ExecutionRecord).where(ExecutionRecord.state == ExecutionState.COMPLETED.name, ExecutionRecord.mode == mode)'),
    
    (r'def get_pnl_by_strategy\(self\) -> dict:', r'def get_pnl_by_strategy(self, mode: str = None) -> dict:\n        mode = mode or self.active_mode', r'stmt = select\(ExecutionRecord\).where\(ExecutionRecord.state == ExecutionState.COMPLETED.name\)', r'stmt = select(ExecutionRecord).where(ExecutionRecord.state == ExecutionState.COMPLETED.name, ExecutionRecord.mode == mode)'),
    
    (r'def get_all_executions\(self, strategy: str = None\) -> List\[dict\]:', r'def get_all_executions(self, strategy: str = None, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode', r'stmt = select\(ExecutionRecord\)', r'stmt = select(ExecutionRecord).where(ExecutionRecord.mode == mode)'),
    
    (r'def get_all_reconciliation_logs\(self\) -> List\[dict\]:', r'def get_all_reconciliation_logs(self, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode', r'stmt = select\(ReconciliationLog\)', r'stmt = select(ReconciliationLog).where(ReconciliationLog.mode == mode)')
]

for q_def, new_q_def, q_stmt, new_q_stmt in query_updates:
    content = re.sub(q_def, new_q_def, content)
    content = re.sub(q_stmt, new_q_stmt, content)

with open("core/database.py", "w") as f:
    f.write(content)
