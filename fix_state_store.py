import asyncio
import os

def fix():
    with open("core/state_store.py", "r") as f:
        text = f.read()
        
    # Remove the wrongly appended methods at the end
    if "@abstractmethod\n    async def get_pnl" in text:
        text = text.split("@abstractmethod\n    async def get_pnl")[0]
        
    # Insert abstract methods into StateStore
    if "async def get_pnl(self, mode: str = None) -> float:" not in text:
        marker = "class InMemoryStateStore(StateStore):"
        parts = text.split(marker)
        
        abstract_methods = """
    @abstractmethod
    async def get_pnl(self, mode: str = None) -> float:
        pass
        
    @abstractmethod
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        pass

"""
        new_text = parts[0] + abstract_methods + marker + parts[1]
        
        # Now append concrete methods to InMemoryStateStore
        concrete_methods = """
    async def get_pnl(self, mode: str = None) -> float:
        return 0.0
        
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        return {}
"""
        new_text = new_text + concrete_methods
        
        with open("core/state_store.py", "w") as f:
            f.write(new_text)

if __name__ == "__main__":
    fix()
