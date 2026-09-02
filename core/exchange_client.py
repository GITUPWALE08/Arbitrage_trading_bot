from abc import ABC, abstractmethod

class ExchangeClient(ABC):
    """
    Unified wrapper per exchange.
    """
    
    @abstractmethod
    async def get_balances(self) -> dict:
        """
        Fetch actual balances from the exchange via REST.
        Returns a dict of {asset: amount}.
        """
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> dict:
        """
        Fetch the current actual status of a specific order from the exchange.
        """
        pass

    @abstractmethod
    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float = None) -> dict:
        """
        Submits an order to the exchange. Returns order info including ID.
        """
        pass

    @abstractmethod
    async def get_historical_funding_rates(self, symbol: str, days_back: int) -> list:
        """
        Fetch historical funding rates for a perpetual symbol.
        Returns a list of dicts: [{'timestamp': int, 'rate': float}]
        """
        pass

    @abstractmethod
    async def get_margin_ratio(self, symbol: str) -> float:
        """
        Returns the current margin ratio for a given perp/margin symbol.
        """
        pass

    @abstractmethod
    async def get_mark_price(self, symbol: str) -> float:
        """
        Fetch the current mark price for a perpetual or spot symbol.
        """
        pass

