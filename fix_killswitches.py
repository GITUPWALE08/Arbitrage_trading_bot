import asyncio
import os
from core.database import DatabaseStateStore
from core.execution_engine import ExecutionState

async def run():
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_state.db")
    store = DatabaseStateStore(db_url)
    await store.initialize_db()
    
    await store.set_kill_switch("exchange", "bybit", False, "admin", "resetting for first boot")
    await store.set_kill_switch("exchange", "binance", False, "admin", "resetting for first boot")
    print("Kill switches reset!")

if __name__ == "__main__":
    asyncio.run(run())
