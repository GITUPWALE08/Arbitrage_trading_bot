with open("core/state_store.py", "r") as f:
    text = f.read()

class_text = text.split("class InMemoryStateStore(StateStore):")[1]
if "def get_pnl(" not in class_text:
    new_text = text + """
    async def get_pnl(self, mode: str = None) -> float:
        return 0.0
    
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        return {}
"""
    with open("core/state_store.py", "w") as f:
        f.write(new_text)
