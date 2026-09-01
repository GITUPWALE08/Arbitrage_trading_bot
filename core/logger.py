import logging
from typing import Any, Dict

class ExecutionLogger:
    def __init__(self):
        self.logger = logging.getLogger("ExecutionLogger")
        self.logger.setLevel(logging.INFO)
        # In a real setup, we would add handlers here

    def log_transition(self, execution_id: str, old_state: str, new_state: str, data: Dict[str, Any]):
        """
        Logs every state transition with timestamp and context, as required by Section 13.
        """
        self.logger.info(
            f"Transition [{execution_id}]: {old_state} -> {new_state} | Data: {data}"
        )
