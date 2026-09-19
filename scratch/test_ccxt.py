import asyncio
import ccxt.pro as ccxtpro

async def test_ccxt():
    exchange = ccxtpro.binance()
    await exchange.load_markets()
    print("Is BTC/USDT:USDT in markets?", 'BTC/USDT:USDT' in exchange.markets)
    await exchange.close()

asyncio.run(test_ccxt())
