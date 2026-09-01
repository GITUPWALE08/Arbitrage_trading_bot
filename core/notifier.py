from abc import ABC, abstractmethod

class Notifier(ABC):
    @abstractmethod
    async def send_high_priority_alert(self, message: str):
        """
        Send an immediate high-priority alert (e.g., via Telegram/Discord)
        """
        pass

class ConsoleNotifier(Notifier):
    """
    Console notifier for testing before Telegram is wired up.
    """
    async def send_high_priority_alert(self, message: str):
        print(f"🚨 HIGH PRIORITY ALERT: {message}")
