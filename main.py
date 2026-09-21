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
from core.execution_engine import ExecutionStateMachine, ExecutionContext, ExecutionState

from paper_trading.simulator import SimulatedExchangeClient

from strategies.triangular import TriangularArbitrageStrategy
from strategies.cross_exchange import CrossExchangeArbitrageStrategy
from strategies.funding_rate import FundingRateStrategy

from gate.go_live_gate import GoLiveGate, TradeJournalMock, GateConfig

logger = logging.getLogger("Main")
logger.setLevel(logging.INFO)



async def main_trading_loop(
    strategies: Dict,
    risk_manager: RiskManager,
    gate: GoLiveGate,
    fast_store: any,
    state_store=None,
    state_machine=None
):
    """
    The main decision loop. Evaluates strategies and executes trades.
    In paper/simulated mode: executes via SimulatedExchangeClient (no gate check).
    In live mode: requires Go-Live Gate to pass before executing.
    """
    logger.info("Bot is alive and entering main trading loop.")
    active_mode = getattr(state_store, 'active_mode', 'simulated')
    is_live = (active_mode == 'live')
    
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
            
        # 2. Evaluate Strategy A (Triangular)
        if await fast_store.acquire_lock("triangular_eval", timeout_sec=2):
            try:
                tri_strat = strategies['triangular']
                tri_def = [
                    {'symbol': 'BTC/USDT', 'side': 'buy'},
                    {'symbol': 'ETH/BTC', 'side': 'buy'},
                    {'symbol': 'ETH/USDT', 'side': 'sell'}
                ]
                tri_eval = await tri_strat.evaluate_triangle(tri_def, 200.0)
                
                if tri_eval.get('is_viable'):
                    # In paper mode, execute directly. In live mode, require gate.
                    should_execute = True
                    if is_live:
                        passed_gate, gate_reason = gate.evaluate('triangular')
                        if not passed_gate:
                            logger.info(f"Go-Live Gate blocks live triangular execution: {gate_reason}")
                            should_execute = False
                    
                    if should_execute and state_machine:
                        import uuid
                        ctx = ExecutionContext(
                            execution_id=str(uuid.uuid4()),
                            strategy="triangular",
                            data={"triangle_def": [d['symbol'] for d in tri_def]}
                        )
                        await state_machine.transition(ctx, ExecutionState.OPPORTUNITY_DETECTED)
                        try:
                            await tri_strat.execute_triangle(ctx, tri_eval.get('legs', []))
                            logger.info(f"Triangular execution {ctx.execution_id[:8]} completed: {ctx.state}")
                        except Exception as e:
                            logger.error(f"Triangular execution failed: {e}")
            except Exception as e:
                logger.error(f"Error evaluating Strategy A: {e}")
            finally:
                await fast_store.release_lock("triangular_eval")
                
        # 3. Evaluate Strategy B (Cross-Exchange) — check BOTH directions
        if await fast_store.acquire_lock("cross_exchange_eval", timeout_sec=2):
            try:
                ce_strat = strategies['cross_exchange']
                
                # Get current BTC price for dynamic sizing
                obm = ce_strat.orderbook_manager
                binance_book = await obm.get_book('binance', 'BTC/USDT')
                btc_price = binance_book.asks[0][0] if binance_book and binance_book.asks else 60000.0
                dynamic_size = min(ce_strat.max_position_size_usd / btc_price, 0.01)  # Cap at sensible amount
                
                # Check both directions
                for buy_ex, sell_ex in [('binance', 'bybit'), ('bybit', 'binance')]:
                    ce_eval = await ce_strat.evaluate_opportunity('BTC/USDT', buy_ex, sell_ex, dynamic_size)
                    if ce_eval.get('is_viable'):
                        should_execute = True
                        if is_live:
                            passed_gate, gate_reason = gate.evaluate('cross_exchange')
                            if not passed_gate:
                                logger.info(f"Go-Live Gate blocks live cross-exchange execution: {gate_reason}")
                                should_execute = False
                        
                        if should_execute and state_machine:
                            import uuid
                            ctx = ExecutionContext(
                                execution_id=str(uuid.uuid4()),
                                strategy="cross_exchange",
                                data={"symbol": "BTC/USDT", "buy_exchange": buy_ex, "sell_exchange": sell_ex}
                            )
                            await state_machine.transition(ctx, ExecutionState.OPPORTUNITY_DETECTED)
                            try:
                                await ce_strat.execute_arbitrage(ctx, ce_eval.get('legs', []))
                                logger.info(f"Cross-exchange execution {ctx.execution_id[:8]} completed: {ctx.state}")
                            except Exception as e:
                                logger.error(f"Cross-exchange execution failed: {e}")
                        break  # Only execute the first viable direction per cycle
            except Exception as e:
                logger.error(f"Error evaluating Strategy B: {e}")
            finally:
                await fast_store.release_lock("cross_exchange_eval")
                
        # 4. Evaluate Strategy C (Funding Rate) — entry evaluation + execution
        if await fast_store.acquire_lock("funding_rate_eval", timeout_sec=2):
            try:
                fr_strat = strategies['funding_rate']
                fr_eval = await fr_strat.evaluate_entry('BTC/USDT', 'BTC/USDT:USDT')
                if fr_eval.get('enter'):
                    should_execute = True
                    if is_live:
                        passed_gate, gate_reason = gate.evaluate('funding_rate')
                        if not passed_gate:
                            logger.info(f"Go-Live Gate blocks live funding rate execution: {gate_reason}")
                            should_execute = False
                    
                    if should_execute and state_machine:
                        import uuid
                        ctx = ExecutionContext(
                            execution_id=str(uuid.uuid4()),
                            strategy="funding_rate",
                            data={
                                "spot_symbol": "BTC/USDT",
                                "perp_symbol": "BTC/USDT:USDT",
                                "avg_annualized_pct": fr_eval.get('avg_annualized_pct', 0),
                                "basis_pct": fr_eval.get('basis_pct', 0),
                            }
                        )
                        await state_machine.transition(ctx, ExecutionState.OPPORTUNITY_DETECTED)
                        try:
                            await fr_strat.execute_entry(ctx, 'BTC/USDT', 'BTC/USDT:USDT', fr_eval['spot_price'])
                            logger.info(f"Funding rate entry {ctx.execution_id[:8]} completed: {ctx.state}")
                        except Exception as e:
                            logger.error(f"Funding rate entry execution failed: {e}")
            except Exception as e:
                logger.error(f"Error evaluating Strategy C: {e}")
            finally:
                await fast_store.release_lock("funding_rate_eval")
        
        # 5. Monitor active funding rate positions for exit signals
        if await fast_store.acquire_lock("funding_rate_exit_eval", timeout_sec=2):
            try:
                fr_strat = strategies['funding_rate']
                for position_key, position in list(fr_strat.active_positions.items()):
                    exit_eval = await fr_strat.evaluate_exit(
                        position['spot_symbol'], position['perp_symbol'], position['entry_time']
                    )
                    if exit_eval.get('exit'):
                        logger.info(f"Funding rate exit triggered for {position_key}: {exit_eval['reason']}")
                        if state_machine:
                            import uuid
                            ctx = ExecutionContext(
                                execution_id=str(uuid.uuid4()),
                                strategy="funding_rate_exit",
                                data={"position_key": position_key, "reason": exit_eval['reason']}
                            )
                            await state_machine.transition(ctx, ExecutionState.OPPORTUNITY_DETECTED)
                            try:
                                await fr_strat.execute_exit(ctx, position_key)
                                logger.info(f"Funding rate exit {ctx.execution_id[:8]} completed: {ctx.state}")
                            except Exception as e:
                                logger.error(f"Funding rate exit execution failed: {e}")
            except Exception as e:
                logger.error(f"Error monitoring funding rate positions: {e}")
            finally:
                await fast_store.release_lock("funding_rate_exit_eval")
                
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
        # Lift the global kill switch that was engaged to safely reboot
        await state_store.set_kill_switch("global", "global", False, "System", "Mode switch complete")
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
        'exchanges': ['binance', 'bybit'],
        'funding_rate': {
            'funding_history_window_days': 10,
            'min_trailing_annualized_funding_pct': 10.0,
            'max_negative_flips_in_window': 2,
            'max_basis_pct': 0.5,
            'min_holding_period_hr': 24,
            'max_holding_period_days': 14,
            'position_size_usd': 500.0,
        }
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
            env_prefix = "LIVE"
            environment = "live"
        elif active_mode == "demo":
            logger.info("🧪 DEMO TRADING MODE ENGAGED. Using CCXTExchangeClient with demo routing.")
            env_prefix = "DEMO"
            environment = "demo"
        else:
            logger.info("🧪 TESTNET TRADING MODE ENGAGED. Using CCXTExchangeClient with testnet=True.")
            env_prefix = "TESTNET"
            environment = "testnet"
            
        from core.ccxt_client import CCXTExchangeClient
        binance_key = os.getenv(f"BINANCE_{env_prefix}_API_KEY", "")
        binance_sec = os.getenv(f"BINANCE_{env_prefix}_SECRET_KEY", "")
        bybit_key = os.getenv(f"BYBIT_{env_prefix}_API_KEY", "")
        bybit_sec = os.getenv(f"BYBIT_{env_prefix}_SECRET_KEY", "")
        
        client_binance = CCXTExchangeClient("binance", binance_key, binance_sec, environment=environment)
        client_bybit = CCXTExchangeClient("bybit", bybit_key, bybit_sec, environment=environment)
        
        # Load markets before starting websockets
        await client_binance.initialize()
        await client_bybit.initialize()
        
        clients['binance'] = client_binance
        clients['bybit'] = client_bybit
        
        # We need to watch symbols used by strategies
        symbols_to_watch = ["BTC/USDT", "ETH/BTC", "ETH/USDT"] # CCXT requires a slash
        for sym in symbols_to_watch:
            ws_tasks.append(asyncio.create_task(client_binance.watch_order_book_loop(sym, obm, ws_manager)))
            if sym == "BTC/USDT":
                ws_tasks.append(asyncio.create_task(client_bybit.watch_order_book_loop(sym, obm, ws_manager)))
    else:
        logger.info("📄 PAPER TRADING MODE. Using SimulatedExchangeClient with real live public market data.")
        client_binance = SimulatedExchangeClient("binance", obm, simulated_latency_ms=100)
        client_bybit = SimulatedExchangeClient("bybit", obm, simulated_latency_ms=100)
        clients['binance'] = client_binance
        clients['bybit'] = client_bybit
        
        # Instantiate public CCXT clients strictly for the websocket orderbook feeds
        from core.ccxt_client import CCXTExchangeClient
        public_binance = CCXTExchangeClient("binance", public_only=True)
        public_bybit = CCXTExchangeClient("bybit", public_only=True)
        
        client_binance.public_client = public_binance
        client_bybit.public_client = public_bybit
        
        await public_binance.initialize()
        await public_bybit.initialize()
        
        symbols_to_watch = ["BTC/USDT", "ETH/BTC", "ETH/USDT", "BTC/USDT:USDT"]
        for sym in symbols_to_watch:
            ws_tasks.append(asyncio.create_task(public_binance.watch_order_book_loop(sym, obm, ws_manager)))
            if sym == "BTC/USDT":
                ws_tasks.append(asyncio.create_task(public_bybit.watch_order_book_loop(sym, obm, ws_manager)))
    
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
        "funding_rate": FundingRateStrategy(client_binance, fee_calc, config.get('funding_rate', {}), state_store, state_machine)
    }

    
    # 5.5 CRASH RECOVERY (Section 2.3)
    logger.info("Running startup crash-recovery checks...")
    in_flight = await state_store.get_active_executions()
    recovery_report = []
    
    if not in_flight:
        recovery_report.append("No in-flight executions found.")
    else:
        for exec_data in in_flight:
            exec_id = exec_data['execution_id']
            strat_name = exec_data['strategy']
            old_state = exec_data['state']
            data = exec_data.get('data', {})
            
            logger.warning(f"Recovering execution {exec_id} in state {old_state}")
            
            # Re-fetch actual order statuses if we have order IDs
            filled_legs = data.get('filled_legs', [])
            all_closed = True
            
            for leg in filled_legs:
                order_id = leg.get('order_id')
                symbol = leg.get('symbol')
                exchange = leg.get('exchange')
                if order_id and symbol and exchange in clients:
                    try:
                        status_data = await clients[exchange].get_order_status(order_id, symbol)
                        leg['status'] = status_data['status']
                        if status_data['status'] not in ['closed', 'canceled']:
                            all_closed = False
                    except Exception as e:
                        logger.error(f"Failed to fetch order status for {order_id} on {exchange}: {e}")
                        all_closed = False
                else:
                    all_closed = False
            
            from core.execution_engine import ExecutionContext, ExecutionState
            ctx = ExecutionContext(execution_id=exec_id, strategy=strat_name, state=ExecutionState(old_state), data=data)
            
            # Resolve to STUCK and alert
            msg = f"Recovered {exec_id} ({strat_name}) from {old_state}. Marking STUCK for manual review."
            recovery_report.append(msg)
            
            await state_machine.transition(ctx, ExecutionState.STUCK, data_updates={"recovery": "processed_on_boot", "filled_legs": filled_legs})

    report_str = "\n".join(recovery_report)
    logger.info(f"Crash Recovery Report:\n{report_str}")
    await notifier.send_high_priority_alert(f"Bot Started. Recovery Report:\n{report_str}")


    # 6. Start Async Background Tasks
    tasks = [
        asyncio.create_task(ws_manager.monitor_heartbeats()),
        asyncio.create_task(recon_manager.run_periodic_reconciliation(interval_seconds=60)),
        asyncio.create_task(liq_monitor.monitor_loop()),
        asyncio.create_task(main_trading_loop(strategies, risk_manager, gate, fast_store, state_store, state_machine))
    ] + ws_tasks
    
    logger.info("All subsystems initialized. Bot is running.")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
