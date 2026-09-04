import asyncio
import time
from typing import Dict, Any, List
import ccxt.pro as ccxtpro
import ccxt
from core.exchange_client import ExchangeClient
from core.logger import logging

logger = logging.getLogger("CCXTClient")
logger.setLevel(logging.INFO)

class CCXTExchangeClient(ExchangeClient):
    """
    Live trading ExchangeClient implementation wrapping ccxt.pro.
    """
    def __init__(self, exchange_id: str, api_key: str, secret: str, environment: str = 'live'):
        self.exchange_id = exchange_id
        # Initialize ccxt.pro exchange
        exchange_class = getattr(ccxtpro, exchange_id)
        self.client = exchange_class({
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
        })
        
        if environment == 'testnet':
            self.client.set_sandbox_mode(True)
        elif environment == 'demo':
            # For Bybit Demo Trading or Binance Demo, we manually override the URLs
            if 'demo' in self.client.urls:
                self.client.urls['api'] = self.client.urls['demo']
            elif 'demotrading' in self.client.urls:
                self.client.urls['api'] = self.client.urls['demotrading']
            else:
                # Fallback to testnet if no explicit demo URLs exist
                self.client.set_sandbox_mode(True)

    async def initialize(self):
        """Loads exchange markets before use."""
        try:
            await self.client.load_markets()
            logger.info(f"Loaded markets for {self.exchange_id}")
        except Exception as e:
            logger.error(f"Failed to load markets for {self.exchange_id}: {e}")

    async def close(self):
        await self.client.close()

    async def get_balances(self) -> dict:
        try:
            balances = await self.client.fetch_balance()
            # Returns dictionary { 'BTC': 1.0, 'USDT': 1000.0, ... }
            return {asset: data['free'] for asset, data in balances.items() if isinstance(data, dict) and 'free' in data}
        except Exception as e:
            logger.error(f"Error fetching balances from {self.exchange_id}: {e}")
            raise

    async def get_order_status(self, order_id: str, symbol: str) -> dict:
        try:
            order = await self.client.fetch_order(order_id, symbol)
            return {
                "id": order['id'],
                "status": order['status'], # 'open', 'closed', 'canceled'
                "filled_qty": order['filled'],
                "average_price": order['average']
            }
        except Exception as e:
            logger.error(f"Error fetching order {order_id} on {self.exchange_id}: {e}")
            raise

    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float = None) -> dict:
        try:
            order = await self.client.create_order(symbol, order_type, side, quantity, price)
            return {
                "id": order['id'],
                "status": order['status'],
                "filled_qty": order.get('filled', 0.0),
                "average_price": order.get('average', 0.0)
            }
        except Exception as e:
            logger.error(f"Order placement failed on {self.exchange_id}: {e}")
            raise

    async def get_historical_funding_rates(self, symbol: str, days_back: int) -> list:
        try:
            # ccxt fetchFundingRateHistory
            since = int((time.time() - (days_back * 86400)) * 1000)
            rates = await self.client.fetch_funding_rate_history(symbol, since=since)
            return [{'timestamp': r['timestamp'], 'rate': r['fundingRate']} for r in rates]
        except Exception as e:
            logger.error(f"Error fetching funding rates on {self.exchange_id}: {e}")
            return []

    async def get_margin_ratio(self, symbol: str) -> float:
        try:
            # Varies wildly by exchange in CCXT. We approximate or fetch specific position info.
            # Usually require fetch_positions()
            positions = await self.client.fetch_positions([symbol])
            if positions:
                pos = positions[0]
                # Try to extract margin ratio or liquidation info
                margin_ratio = pos.get('marginRatio', 0.0)
                if margin_ratio is None:
                    # fallback
                    margin_ratio = 0.1
                return float(margin_ratio)
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching margin ratio on {self.exchange_id}: {e}")
            return 0.0

    async def get_mark_price(self, symbol: str) -> float:
        try:
            ticker = await self.client.fetch_ticker(symbol)
            # Some exchanges return markPrice directly in ticker or info
            if 'markPrice' in ticker:
                return float(ticker['markPrice'])
            return float(ticker['last'])
        except Exception as e:
            logger.error(f"Error fetching mark price on {self.exchange_id}: {e}")
            raise

    async def watch_order_book_loop(self, symbol: str, ob_manager, ws_manager):
        """
        Runs continuously in the background, feeding the OrderBookManager.
        """
        while True:
            try:
                ob = await self.client.watch_order_book(symbol)
                # Parse ccxt orderbook format to our tuple format
                bids = [(float(b[0]), float(b[1])) for b in ob.get('bids', [])[:10]]
                asks = [(float(a[0]), float(a[1])) for a in ob.get('asks', [])[:10]]
                await ob_manager.update_book(self.exchange_id, symbol, bids, asks)
                
                # Record heartbeat for stale-data protection
                await ws_manager.record_heartbeat(self.exchange_id)
            except ccxt.NetworkError as e:
                logger.warning(f"Network error watching {symbol} on {self.exchange_id}: {e}. Reconnecting...")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error watching {symbol} on {self.exchange_id}: {e}")
                await asyncio.sleep(5)
