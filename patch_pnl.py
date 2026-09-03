import asyncio

def patch():
    with open("core/state_store.py", "r") as f:
        text = f.read()
    if "def get_pnl" not in text:
        pnl_methods = """
    @abstractmethod
    async def get_pnl(self, mode: str = None) -> float: pass
    
    @abstractmethod
    async def get_pnl_by_strategy(self, mode: str = None) -> dict: pass
"""
        text += pnl_methods
        with open("core/state_store.py", "w") as f:
            f.write(text)

    with open("core/database.py", "r") as f:
        text = f.read()

    if "def get_pnl" not in text:
        pnl_methods_db = """
    async def get_pnl(self, mode: str = None) -> float:
        mode = mode or getattr(self, "active_mode", "simulated")
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(ExecutionRecord).where(
                ExecutionRecord.state == "COMPLETED",
                ExecutionRecord.mode == mode
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            return sum([r.realized_profit for r in records if r.realized_profit])
            
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        mode = mode or getattr(self, "active_mode", "simulated")
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(ExecutionRecord).where(
                ExecutionRecord.state == "COMPLETED",
                ExecutionRecord.mode == mode
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            
            pnl_map = {}
            for r in records:
                if r.realized_profit:
                    pnl_map[r.strategy] = pnl_map.get(r.strategy, 0.0) + r.realized_profit
            return pnl_map
"""
        text += pnl_methods_db
        with open("core/database.py", "w") as f:
            f.write(text)

if __name__ == "__main__":
    patch()
