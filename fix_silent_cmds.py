import asyncio

def patch():
    # 1. Fix the missing methods in StateStore and DatabaseStateStore
    with open("core/state_store.py", "r") as f:
        text = f.read()

    missing_abstracts = """
    @abstractmethod
    async def get_latest_balances(self) -> dict: pass
    
    @abstractmethod
    async def get_recent_opportunities(self) -> list: pass
    
    @abstractmethod
    async def get_recent_executions(self) -> list: pass
    
    @abstractmethod
    async def get_all_kill_switches(self) -> list: pass
"""
    if "def get_latest_balances" not in text:
        marker = "class InMemoryStateStore(StateStore):"
        parts = text.split(marker)
        new_text = parts[0] + missing_abstracts + marker + parts[1]
        
        missing_concretes = """
    async def get_latest_balances(self) -> dict: return {}
    async def get_recent_opportunities(self) -> list: return []
    async def get_recent_executions(self) -> list: return []
    async def get_all_kill_switches(self) -> list: return []
"""
        new_text += missing_concretes
        with open("core/state_store.py", "w") as f:
            f.write(new_text)

    with open("core/database.py", "r") as f:
        text_db = f.read()

    missing_db_methods = """
    async def get_latest_balances(self) -> dict:
        async with self.SessionLocal() as session:
            from sqlalchemy import select, desc
            stmt = select(BalancesSnapshot).order_by(desc(BalancesSnapshot.snapshot_at))
            result = await session.execute(stmt)
            records = result.scalars().all()
            # Just group by exchange and asset (naive version for MVP)
            bals = {}
            for r in records:
                if r.exchange not in bals: bals[r.exchange] = {}
                if r.asset not in bals[r.exchange]: bals[r.exchange][r.asset] = r.balance
            return bals

    async def get_recent_opportunities(self) -> list:
        async with self.SessionLocal() as session:
            from sqlalchemy import select, desc
            stmt = select(OpportunityRecord).order_by(desc(OpportunityRecord.id)).limit(5)
            result = await session.execute(stmt)
            return [{"strategy": r.strategy, "gross_profit": r.gross_profit, "net_profit": r.net_profit} for r in result.scalars().all()]

    async def get_recent_executions(self) -> list:
        async with self.SessionLocal() as session:
            from sqlalchemy import select, desc
            stmt = select(ExecutionRecord).order_by(desc(ExecutionRecord.id)).limit(5)
            result = await session.execute(stmt)
            return [{"execution_id": r.execution_id, "strategy": r.strategy, "state": r.state, "profit": r.realized_profit} for r in result.scalars().all()]

    async def get_all_kill_switches(self) -> list:
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(KillSwitchRecord)
            result = await session.execute(stmt)
            return [{"scope": r.scope, "scope_value": r.scope_value, "is_tripped": r.is_tripped} for r in result.scalars().all()]
"""
    if "def get_latest_balances" not in text_db:
        with open("core/database.py", "a") as f:
            f.write(missing_db_methods)

    # 2. Add mode and switch_demo to notifier.py cmds
    with open("core/notifier.py", "r") as f:
        text_notif = f.read()
    
    if '("mode", self.cmd_mode)' not in text_notif:
        text_notif = text_notif.replace(
            '("switch_testnet", self.cmd_switch_testnet),',
            '("mode", self.cmd_mode),\n                  ("switch_demo", self.cmd_switch_demo),\n                  ("switch_testnet", self.cmd_switch_testnet),'
        )
        with open("core/notifier.py", "w") as f:
            f.write(text_notif)

if __name__ == "__main__":
    patch()
