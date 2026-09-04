import asyncio
import os
import sys
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path)
import random
from typing import Dict, List
from datetime import datetime, timezone

from core.database import DatabaseStateStore
from core.logger import ExecutionLogger, logging
from core.orderbook_manager import OrderBookManager
from core.fee_calculator import FeeCalculator
from core.latency_monitor import LatencyMonitor
from core.inventory_manager import CrossExchangeInventoryManager
from core.risk_manager import RiskManager
from core.reconciliation import ReconciliationManager
from core.liquidation_monitor import LiquidationMonitor
from core.execution_engine import ExecutionStateMachine

from paper_trading.simulator import SimulatedExchangeClient

from strategies.triangular import TriangularArbitrageStrategy
from strategies.cross_exchange import CrossExchangeArbitrageStrategy
from strategies.funding_rate import FundingRateStrategy

from gate.go_live_gate import GoLiveGate, TradeJournalMock, GateConfig

logger = logging.getLogger("Main")
logger.setLevel(logging.INFO)

async def mock_orderbook_websocket_feed(obm: OrderBookManager):
    """
    Simulates a WebSocket feed updating the order books every 100ms.
    In production, this is replaced by ccxt.pro's watch_order_book().
    """
    logger.info("Starting WebSocket feed simulation...")
    while True:
        # Mock some dynamic prices around 50k for BTC
        btc_price = 50000.0 + random.uniform(-10, 10)
        await obm.update_book("binance", "BTCUSDT", [(btc_price - 0.1, 1.5)], [(btc_price + 0.1, 1.5)])
        
        btc_bybit = btc_price + random.uniform(50, 100) # Arbitrage gap!
        await obm.update_book("bybit", "BTCUSDT", [(btc_bybit - 0.1, 1.0)], [(btc_bybit + 0.1, 1.0)])
        
        await obm.update_book("binance", "ETHBTC", [(0.05, 10.0)], [(0.0501, 10.0)])
        await obm.update_book("binance", "ETHUSDT", [(btc_price * 0.05, 10.0)], [(btc_price * 0.05 + 1.0, 10.0)])
        
        await asyncio.sleep(0.1)

async def main_trading_loop(
    strategies: Dict,
    risk_manager: RiskManager,
    gate: GoLiveGate,
    fast_store: any,
    state_store=None
):
    """
    The main decision loop. Evaluates strategies sequentially or in parallel.
    """
    logger.info("Bot is alive and entering main trading loop.")
    
    while True:

        # Check for pending mode switch
        pending_mode = await state_store.get_system_setting("pending_mode")
        if pending_mode:
            execs = await state_store.get_active_executions()
            if not execs:
                logger.info(f"Safe to switch to {pending_mode}. Triggering restart.")
                os._exit(0)

        # 1. Check Global Risk Switches (Section 6.2)
        can_trade = await risk_manager.check_kill_switches()
        if not can_trade:
            logger.warning("Trading paused due to kill switches. Waiting...")
            await asyncio.sleep(5)
            continue
            
        # 2. Acquire distributed lock for evaluation (Section 11)
        if await fast_store.acquire_lock("triangular_eval", timeout_sec=2):
            try:
                # 3. Evaluate Strategy A (Triangular)
                tri_strat = strategies['triangular']
                tri_def = [
                    {'symbol': 'BTCUSDT', 'side': 'buy'},
                    {'symbol': 'ETHBTC', 'side': 'buy'},
                    {'symbol': 'ETHUSDT', 'side': 'sell'}
                ]
                tri_eval = await tri_strat.evaluate_triangle(tri_def, 200.0)
                
                if tri_eval.get('is_viable'):
                    passed_gate, _ = gate.evaluate('triangular')
                    if not passed_gate:
                        logger.debug("Go-Live Gate prevents live execution. Strategy A is viable in paper.")
            finally:
                await fast_store.release_lock("triangular_eval")
                
        await asyncio.sleep(1.0) # Throttle evaluation cycle

async def run_bot():
    """
    Section 17: Application Bootstrap
    """
    logger.info("Bootstrapping Crypto Arbitrage Bot...")
    
    # 1. State & Infra
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_state.db")
    state_store = DatabaseStateStore(db_url)
    await state_store.initialize_db()

    # Process mode switch on startup
    pending_mode = await state_store.get_system_setting("pending_mode")
    if pending_mode:
        await state_store.set_system_setting("active_mode", pending_mode)
        await state_store.delete_system_setting("pending_mode")
        logger.info(f"Applied pending mode switch: {pending_mode}")
        
    active_mode = await state_store.get_system_setting("active_mode")
    if not active_mode:
        active_mode = "simulated"
        await state_store.set_system_setting("active_mode", active_mode)
    state_store.active_mode = active_mode
    logger.info(f"Bot starting in mode: {active_mode.upper()}")

    
    from core.notifier import TelegramNotifier
    
    config = {
        'max_position_size_usd': 500.0,
        'min_profit_threshold_pct': 0.15,
        'slippage_buffer_pct': 0.05,
        'partial_fill_min_viable_pct': 50.0,
        'withdrawal_fee_usd': 5.0,
        'exchanges': ['binance', 'bybit']
    }

    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    notifier = TelegramNotifier(telegram_token, telegram_chat_id, state_store, config)
    await notifier.start()
    
    exec_logger = ExecutionLogger()
    
    risk_manager = RiskManager(state_store, notifier, config)
    state_machine = ExecutionStateMachine(state_store, notifier, exec_logger)
    
    from core.redis_client import FastStateStore
    from core.websocket_manager import WebSocketConnectionManager
    
    fast_store = FastStateStore(os.getenv("REDIS_URL", "redis://localhost"))
    await fast_store.connect()
    
    obm = OrderBookManager(fast_store=fast_store, stale_threshold_sec=0.5)
    ws_manager = WebSocketConnectionManager()
    latency_monitor = LatencyMonitor()
    fee_calc = FeeCalculator(config)
    inventory_manager = CrossExchangeInventoryManager(state_store, config)
    
    # 3. Exchange Clients
    # Check what mode we are in
    is_live = (active_mode == "live")
    is_testnet = (active_mode in ["testnet", "demo"])
    
    clients = {}
    ws_tasks = []
    
    obm = OrderBookManager(fast_store=fast_store, stale_threshold_sec=0.5)
    
    if is_live or is_testnet:
        if is_live:
            logger.warning("🚨 LIVE TRADING MODE ENGAGED. Using CCXTExchangeClient.")
        else:
            logger.info(f"🧪 {active_mode.upper()} TRADING MODE ENGAGED. Using CCXTExchangeClient with testnet=True.")
            
        from core.ccxt_client import CCXTExchangeClient
        binance_key = os.getenv("BINANCE_API_KEY", "")
        binance_sec = os.getenv("BINANCE_SECRET", "")
        bybit_key = os.getenv("BYBIT_API_KEY", "")
        bybit_sec = os.getenv("BYBIT_SECRET", "")
        
        client_binance = CCXTExchangeClient("binance", binance_key, binance_sec, testnet=is_testnet)
        client_bybit = CCXTExchangeClient("bybit", bybit_key, bybit_sec, testnet=is_testnet)
        
        clients['binance'] = client_binance
        clients['bybit'] = client_bybit
        
        # We need to watch symbols used by strategies
        symbols_to_watch = ["BTC/USDT", "ETH/BTC", "ETH/USDT"] # CCXT requires a slash
        for sym in symbols_to_watch:
            ws_tasks.append(asyncio.create_task(client_binance.watch_order_book_loop(sym, obm, ws_manager)))
            if sym == "BTC/USDT":
                ws_tasks.append(asyncio.create_task(client_bybit.watch_order_book_loop(sym, obm, ws_manager)))
    else:
        logger.info("📄 PAPER TRADING MODE. Using SimulatedExchangeClient.")
        client_binance = SimulatedExchangeClient("binance", obm, simulated_latency_ms=100)
        client_bybit = SimulatedExchangeClient("bybit", obm, simulated_latency_ms=100)
        clients['binance'] = client_binance
        clients['bybit'] = client_bybit
        ws_tasks.append(asyncio.create_task(mock_orderbook_websocket_feed(obm)))
        
    recon_manager = ReconciliationManager(state_store, clients, notifier)
    recon_manager.risk_manager = risk_manager # Circular dep injection

    liq_monitor = LiquidationMonitor(client_binance, notifier, state_machine)
    liq_monitor.risk_manager = risk_manager
    
    class DummyJournal:
        executions = []
        reconciliation_logs = []
        paper_trading_start = datetime.now(timezone.utc)
    
    # Normally we would query the state_store for this in the main loop,
    # but the GoLiveGate now requires a journal-like object to read from.
    # We will initialize it dynamically in the command or pass an object that pulls from state_store.
    # For main.py initialization, we can just pass the dummy since evaluate() is only called in notifier or after querying DB.
    gate_journal = DummyJournal()
    gate = GoLiveGate(gate_journal, GateConfig(manual_sign_off=False))

    # 5. Strategies
    strategies = {
        "triangular": TriangularArbitrageStrategy(client_binance, fee_calc, obm, state_machine, config),
        "cross_exchange": CrossExchangeArbitrageStrategy(clients, fee_calc, obm, state_machine, inventory_manager, config),
        "funding_rate": FundingRateStrategy(client_binance, fee_calc, config.get('funding_rate', {}), state_store)
    }

    # 6. Start Async Background Tasks
    tasks = [
        asyncio.create_task(ws_manager.monitor_heartbeats()),
        asyncio.create_task(recon_manager.run_periodic_reconciliation(interval_seconds=60)),
        asyncio.create_task(liq_monitor.monitor_loop()),
        asyncio.create_task(main_trading_loop(strategies, risk_manager, gate, fast_store, state_store))
    ] + ws_tasks
    
    logger.info("All subsystems initialized. Bot is running.")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
