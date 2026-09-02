with open("core/database.py", "r") as f:
    text = f.read()

# Add mode column to models
models = ["ExecutionRecord", "ReconciliationLog", "ExecutionLeg", "OpportunityRecord", "BalancesSnapshot", "SystemEvent", "MarginMonitoring"]
for m in models:
    class_def = f"class {m}(Base):"
    idx = text.find(class_def)
    if idx == -1: continue
    next_class = text.find("class ", idx + 10)
    if next_class == -1: next_class = len(text)
    
    part = text[idx:next_class]
    last_col = part.rfind("Column(")
    newline = part.find("\n", last_col)
    
    new_part = part[:newline] + '\n    mode = Column(String, default="simulated")' + part[newline:]
    text = text[:idx] + new_part + text[next_class:]

# Add SystemSettings
sys_settings = """
class SystemSettings(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)

class DatabaseStateStore(StateStore):"""
text = text.replace("class DatabaseStateStore(StateStore):", sys_settings)

# Init
text = text.replace("def __init__(self, db_url: str):", "def __init__(self, db_url: str, active_mode: str = 'simulated'):\n        self.active_mode = active_mode")

# Migration
mig = """            
            # Migration for mode column
            tables = ["executions", "reconciliation_log", "execution_legs", "opportunities", "balances_snapshot", "system_events", "margin_monitoring"]
            from sqlalchemy import text as sqa_text
            for table in tables:
                try:
                    await conn.execute(sqa_text(f"ALTER TABLE {table} ADD COLUMN mode VARCHAR DEFAULT 'simulated'"))
                except Exception:
                    pass
"""
text = text.replace("await conn.run_sync(Base.metadata.create_all)", "await conn.run_sync(Base.metadata.create_all)" + mig)

# Settings methods
settings_methods = """
    async def get_system_setting(self, key: str) -> str:
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(SystemSettings).where(SystemSettings.key == key)
            result = await session.execute(stmt)
            record = result.scalars().first()
            return record.value if record else None

    async def set_system_setting(self, key: str, value: str):
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(SystemSettings).where(SystemSettings.key == key)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if not record:
                record = SystemSettings(key=key, value=value)
                session.add(record)
            else:
                record.value = value
            await session.commit()
            
    async def delete_system_setting(self, key: str):
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(SystemSettings).where(SystemSettings.key == key)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if record:
                await session.delete(record)
                await session.commit()
"""
text = text.replace("    async def save_execution_state(", settings_methods + "\n    async def save_execution_state(")

# Save methods
text = text.replace("record = ExecutionRecord(", "record = ExecutionRecord(mode=self.active_mode, ")
text = text.replace("log = ReconciliationLog(", "log = ReconciliationLog(mode=self.active_mode, ")
text = text.replace("record = ExecutionLeg(**leg_data)", "record = ExecutionLeg(**leg_data, mode=self.active_mode)")
text = text.replace("record = OpportunityRecord(**opp_data)", "record = OpportunityRecord(**opp_data, mode=self.active_mode)")
text = text.replace("record = BalancesSnapshot(exchange=", "record = BalancesSnapshot(mode=self.active_mode, exchange=")
text = text.replace("record = MarginMonitoring(position_id=", "record = MarginMonitoring(mode=self.active_mode, position_id=")
text = text.replace("record = SystemEvent(event_type=", "record = SystemEvent(mode=self.active_mode, event_type=")

# Queries
text = text.replace("def get_execution_state(self, execution_id: str) -> dict:", "def get_execution_state(self, execution_id: str) -> dict:\n        mode = self.active_mode")
text = text.replace("ExecutionRecord.execution_id == execution_id", "ExecutionRecord.execution_id == execution_id, ExecutionRecord.mode == mode")

text = text.replace("def get_active_executions(self) -> List[Dict[str, Any]]:", "def get_active_executions(self) -> List[Dict[str, Any]]:\n        mode = self.active_mode")
text = text.replace("ExecutionRecord.state.in_(active_states)", "ExecutionRecord.state.in_(active_states), ExecutionRecord.mode == mode")

text = text.replace("def get_stuck_positions(self) -> List[dict]:", "def get_stuck_positions(self, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode")
text = text.replace("ExecutionRecord.state == ExecutionState.STUCK.name", "ExecutionRecord.state == ExecutionState.STUCK.name, ExecutionRecord.mode == mode")

text = text.replace("def get_recent_opportunities(self, limit: int = 5) -> List[dict]:", "def get_recent_opportunities(self, limit: int = 5, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode")
text = text.replace("select(OpportunityRecord)", "select(OpportunityRecord).where(OpportunityRecord.mode == mode)")

text = text.replace("def get_recent_executions(self, limit: int = 5) -> List[dict]:", "def get_recent_executions(self, limit: int = 5, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode")
text = text.replace("select(ExecutionRecord).order_by", "select(ExecutionRecord).where(ExecutionRecord.mode == mode).order_by")

text = text.replace("def get_latest_balances(self) -> dict:", "def get_latest_balances(self, mode: str = None) -> dict:\n        mode = mode or self.active_mode")
text = text.replace("select(BalancesSnapshot)", "select(BalancesSnapshot).where(BalancesSnapshot.mode == mode)")

text = text.replace("def get_pnl(self) -> float:", "def get_pnl(self, mode: str = None) -> float:\n        mode = mode or self.active_mode")
text = text.replace("ExecutionRecord.state == ExecutionState.COMPLETED.name", "ExecutionRecord.state == ExecutionState.COMPLETED.name, ExecutionRecord.mode == mode")

text = text.replace("def get_pnl_by_strategy(self) -> dict:", "def get_pnl_by_strategy(self, mode: str = None) -> dict:\n        mode = mode or self.active_mode")
# Already handled by get_pnl

text = text.replace("def get_all_executions(self, strategy: str = None) -> List[dict]:", "def get_all_executions(self, strategy: str = None, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode")
text = text.replace("stmt = select(ExecutionRecord)\n", "stmt = select(ExecutionRecord).where(ExecutionRecord.mode == mode)\n")

text = text.replace("def get_all_reconciliation_logs(self) -> List[dict]:", "def get_all_reconciliation_logs(self, mode: str = None) -> List[dict]:\n        mode = mode or self.active_mode")
text = text.replace("stmt = select(ReconciliationLog)", "stmt = select(ReconciliationLog).where(ReconciliationLog.mode == mode)")

with open("core/database.py", "w") as f:
    f.write(text)
