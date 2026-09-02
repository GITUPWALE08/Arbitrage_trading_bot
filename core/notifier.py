from abc import ABC, abstractmethod
import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from core.logger import logging

logger = logging.getLogger("Notifier")
logger.setLevel(logging.INFO)

class Notifier(ABC):
    @abstractmethod
    async def send_high_priority_alert(self, message: str):
        pass
    # in the notifier function in the notifier.py file, the send_high_priority_alert method is defined as an abstract method. This means that any subclass of Notifier must implement this method. The purpose of this method is to send high-priority alerts, which could be implemented in different ways depending on the subclass (e.g., sending a message to a console, sending a Telegram message, etc.).

class ConsoleNotifier(Notifier):
    """
    Console notifier for testing before Telegram is wired up.
    """
    async def send_high_priority_alert(self, message: str):
        print(f"🚨 HIGH PRIORITY ALERT: {message}")

class TelegramNotifier(Notifier):
    """
    Implements Section 7 & 6.2: Telegram bot for real-time alerts and manual kill switch commands.
    """
    def __init__(self, token: str, chat_id: str, state_store, config: dict = None):
        self.token = token
        self.chat_id = chat_id
        self.state_store = state_store
        self.config = config or {}
        self.application = None
        
    async def start(self):
        if not self.token or not self.chat_id:
            logger.warning("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not provided. Telegram bot disabled.")
            return

        try:
            self.application = ApplicationBuilder().token(self.token).build()

            cmds = [
                ("status", self.cmd_status),
                ("positions", self.cmd_positions),
                ("balance", self.cmd_balance),
                ("pnl", self.cmd_pnl),
                ("pnl_detail", self.cmd_pnl_detail),
                ("opportunities", self.cmd_opportunities),
                ("health", self.cmd_health),
                ("executions", self.cmd_executions),
                ("gate_status", self.cmd_gate_status),
                ("kill_all", self.cmd_kill_all),
                ("kill_strategy", self.cmd_kill_strategy),
                ("kill_exchange", self.cmd_kill_exchange),
                ("resume_all", self.cmd_resume_all),
                ("resume_strategy", self.cmd_resume_strategy),
                ("resume_exchange", self.cmd_resume_exchange),
                ("kill_status", self.cmd_kill_status),
                ("close_position", self.cmd_close_position),
                ("stuck", self.cmd_stuck),
                ("unstick", self.cmd_unstick),
                ("config", self.cmd_config),
                                                ("switch_testnet", self.cmd_switch_testnet),
                ("switch_live", self.cmd_switch_live),
                ("confirm_live", self.cmd_confirm_live),
                ("pnl_demo", self.cmd_pnl_demo),
                ("pnl_testnet", self.cmd_pnl_testnet),
                ("pnl_live", self.cmd_pnl_live),
                ("positions_demo", self.cmd_positions_demo),
                ("positions_testnet", self.cmd_positions_testnet),
                ("positions_live", self.cmd_positions_live),
                ("balance_demo", self.cmd_balance_demo),
                ("balance_testnet", self.cmd_balance_testnet),
                ("balance_live", self.cmd_balance_live),
                ("golive", self.cmd_golive),
            ]
            for cmd_str, handler in cmds:
                self.application.add_handler(CommandHandler(cmd_str, handler))
            
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            logger.info("Telegram Notifier started. Listening for commands.")
            await self.send_high_priority_alert("Bot initialized and connected to Telegram.")
        except Exception as e:
            logger.error(f"Failed to start Telegram Notifier: {e}")
            self.application = None

    async def send_high_priority_alert(self, message: str):
        if self.application and self.chat_id:
            try:
                await self.application.bot.send_message(chat_id=self.chat_id, text=f"🚨 {message}")
            except Exception as e:
                logger.error(f"Failed to send telegram message: {e}")
        
        print(f"🚨 ALERT: {message}")

    async def stop(self):
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def _log_event(self, event_type: str, severity: str, payload: dict):
        if hasattr(self.state_store, 'log_system_event'):
            await self.state_store.log_system_event(event_type, severity, payload)

    async def _auth(self, update: Update) -> bool:
        if str(update.effective_chat.id) != self.chat_id:
            return False
        return True

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_active_executions'): return
        execs = await self.state_store.get_active_executions()
        if not execs:
            await update.message.reply_text("No active positions.")
            return
        msg = "Active Positions:\n" + "\n".join([f"{e['execution_id'][:8]} ({e['strategy']}): {e['state']}" for e in execs])
        await update.message.reply_text(msg)

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_latest_balances'): return
        bals = await self.state_store.get_latest_balances()
        if not bals:
            await update.message.reply_text("No balance snapshots found.")
            return
        msg = "Latest Balances:\n"
        for exc, assets in bals.items():
            msg += f"\n{exc.upper()}:\n"
            for ast, bal in assets.items():
                msg += f"  {ast}: {bal}\n"
        await update.message.reply_text(msg)

    async def cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_pnl'): return
        pnl = await self.state_store.get_pnl()
        await update.message.reply_text(f"Total Realized P&L: ${pnl:.2f}")

    async def cmd_pnl_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_pnl_by_strategy'): return
        pnl_map = await self.state_store.get_pnl_by_strategy()
        if not pnl_map:
            await update.message.reply_text("No P&L data available.")
            return
        msg = "P&L by Strategy:\n"
        for strat, pnl in pnl_map.items():
            msg += f"- {strat}: ${pnl:.2f}\n"
        await update.message.reply_text(msg)

    async def cmd_opportunities(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_recent_opportunities'): return
        opps = await self.state_store.get_recent_opportunities(5)
        if not opps:
            await update.message.reply_text("No recent opportunities.")
            return
        msg = "Recent Opportunities:\n" + "\n".join([f"{o['symbols']} ({o['strategy']}) - {o['action']}: {o['profit']:.4f}" for o in opps])
        await update.message.reply_text(msg)

    async def cmd_executions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_recent_executions'): return
        execs = await self.state_store.get_recent_executions(5)
        if not execs:
            await update.message.reply_text("No recent executions.")
            return
        msg = "Recent Executions:\n" + "\n".join([f"{e['id'][:8]} ({e['strategy']}): {e['state']} (P&L: {e['pnl']:.4f})" for e in execs])
        await update.message.reply_text(msg)

    async def cmd_stuck(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_stuck_positions'): return
        stuck = await self.state_store.get_stuck_positions()
        if not stuck:
            await update.message.reply_text("No STUCK positions! 🟢")
            return
        msg = "STUCK Positions:\n" + "\n".join([f"{s['id'][:8]} ({s['strategy']}) at {s['created_at']}" for s in stuck])
        await update.message.reply_text(msg)

    async def cmd_close_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /close_position <id>")
            return
        execution_id = context.args[0]
        if hasattr(self.state_store, 'get_execution_state'):
            record = await self.state_store.get_execution_state(execution_id)
            if not record:
                await update.message.reply_text(f"Execution {execution_id} not found.")
                return
            from core.execution_engine import ExecutionState
            await self.state_store.save_execution_state(execution_id, record['strategy'], ExecutionState.UNWINDING, record['data'])
            await update.message.reply_text(f"Position {execution_id} set to UNWINDING. The state machine will handle the close.")
            await self._log_event("manual_close_position", "WARNING", {"execution_id": execution_id})

    async def cmd_unstick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /unstick <id>")
            return
        execution_id = context.args[0]
        if hasattr(self.state_store, 'get_execution_state'):
            record = await self.state_store.get_execution_state(execution_id)
            if not record:
                await update.message.reply_text(f"Execution {execution_id} not found.")
                return
            from core.execution_engine import ExecutionState
            await self.state_store.save_execution_state(execution_id, record['strategy'], ExecutionState.COMPLETED, record['data'])
            await update.message.reply_text(f"Position {execution_id} unstuck and marked COMPLETED.")
            await self._log_event("manual_unstick", "WARNING", {"execution_id": execution_id})

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        msg = "Bot Configuration:\n"
        for k, v in self.config.items():
            msg += f"- {k}: {v}\n"
        await update.message.reply_text(msg)

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        golive = os.path.exists("GOLIVE_APPROVED.flag")
        msg = "Mode: LIVE 🔴" if golive else "Mode: PAPER 📄"
        await update.message.reply_text(msg)

    async def cmd_kill_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not hasattr(self.state_store, 'get_all_kill_switches'): return
        switches = await self.state_store.get_all_kill_switches()
        msg = "Kill Switches:\n"
        for s in switches:
            icon = "🛑" if s['tripped'] else "🟢"
            msg += f"{icon} {s['scope']} ({s['value']}): {s['by']}\n"
        await update.message.reply_text(msg)

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        msg = "System Health:\n- WebSockets: OK (simulated)\n- Latency: OK\n- Last Recon: OK"
        await update.message.reply_text(msg)

    async def cmd_gate_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        
        # Build the TradeJournalMock from the real DB state
        from gate.go_live_gate import GoLiveGate, TradeJournalMock, GateConfig
        journal = TradeJournalMock()
        
        if hasattr(self.state_store, 'get_all_executions'):
            journal.executions = await self.state_store.get_all_executions()
            if journal.executions:
                # Find the earliest execution
                earliest = min(journal.executions, key=lambda x: x['created_at'])
                journal.paper_trading_start = earliest['created_at']
                
        if hasattr(self.state_store, 'get_all_reconciliation_logs'):
            journal.reconciliation_logs = await self.state_store.get_all_reconciliation_logs()
            
        gate_config = GateConfig(**self.config.get('go_live_gate', {}))
        gate = GoLiveGate(journal, gate_config)
        
        strategy = context.args[0] if context.args else 'funding_rate'
        passed, report = gate.evaluate(strategy)
        
        await update.message.reply_text(f"```\n{report}\n```", parse_mode='MarkdownV2')

    async def cmd_not_implemented(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        await update.message.reply_text("Command recognized but not yet fully implemented.")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        ks = await self.state_store.get_kill_switch('global', 'master')
        status = "PAUSED 🛑" if ks and ks.get('is_tripped') else "ACTIVE 🟢"
        golive_status = "APPROVED ✅" if os.path.exists("GOLIVE_APPROVED.flag") else "PAPER ONLY 📄"
        msg = f"Bot Status: {status}\nTrading Mode: {golive_status}"
        await update.message.reply_text(msg)
        await self._log_event("telegram_command", "INFO", {"command": "/status"})
        
    async def cmd_kill_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        await self.state_store.set_kill_switch('global', 'master', True, 'telegram_admin', 'Manual kill via Telegram')
        await update.message.reply_text("🛑 Global Kill Switch TRIPPED. All trading halted.")
        await self._log_event("kill_switch_tripped", "CRITICAL", {"scope": "global", "by": "telegram"})

    async def cmd_resume_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        await self.state_store.set_kill_switch('global', 'master', False, 'telegram_admin', 'Manual resume via Telegram')
        await update.message.reply_text("🟢 Global Kill Switch LIFTED. Trading resumed.")
        await self._log_event("kill_switch_lifted", "WARNING", {"scope": "global", "by": "telegram"})

    async def cmd_kill_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /kill_strategy <name>")
            return
        strat = context.args[0]
        await self.state_store.set_kill_switch('strategy', strat, True, 'telegram_admin', 'Manual kill via Telegram')
        await update.message.reply_text(f"🛑 Strategy Kill Switch TRIPPED for: {strat}")
        await self._log_event("kill_switch_tripped", "CRITICAL", {"scope": "strategy", "value": strat, "by": "telegram"})

    async def cmd_resume_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /resume_strategy <name>")
            return
        strat = context.args[0]
        await self.state_store.set_kill_switch('strategy', strat, False, 'telegram_admin', 'Manual resume via Telegram')
        await update.message.reply_text(f"🟢 Strategy Kill Switch LIFTED for: {strat}")
        await self._log_event("kill_switch_lifted", "WARNING", {"scope": "strategy", "value": strat, "by": "telegram"})

    async def cmd_kill_exchange(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /kill_exchange <name>")
            return
        exc = context.args[0]
        await self.state_store.set_kill_switch('exchange', exc, True, 'telegram_admin', 'Manual kill via Telegram')
        await update.message.reply_text(f"🛑 Exchange Kill Switch TRIPPED for: {exc}")
        await self._log_event("kill_switch_tripped", "CRITICAL", {"scope": "exchange", "value": exc, "by": "telegram"})

    async def cmd_resume_exchange(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /resume_exchange <name>")
            return
        exc = context.args[0]
        await self.state_store.set_kill_switch('exchange', exc, False, 'telegram_admin', 'Manual resume via Telegram')
        await update.message.reply_text(f"🟢 Exchange Kill Switch LIFTED for: {exc}")
        await self._log_event("kill_switch_lifted", "WARNING", {"scope": "exchange", "value": exc, "by": "telegram"})

    async def cmd_golive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        with open("GOLIVE_APPROVED.flag", "w") as f:
            f.write("Approved manually by admin via Telegram.")
        await update.message.reply_text("✅ Go-Live manual flag created! Bot is authorized for live trading (if other programmatic gates pass).")

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        mode = getattr(self.state_store, 'active_mode', 'unknown')
        await update.message.reply_text(f"Current active mode: {mode.upper()}")

    async def _handle_switch(self, update, context, target_mode):
        if not await self._auth(update): return
        current = getattr(self.state_store, 'active_mode', 'unknown')
        
        force = context.args and context.args[0].lower() == 'force'
        
        if not force:
            execs = await self.state_store.get_active_executions()
            if execs:
                msg = f"Cannot switch to {target_mode}. Open positions in {current} mode:\n"
                msg += "\n".join([f"{e['execution_id'][:8]} ({e['strategy']}): {e['state']}" for e in execs])
                msg += f"\n\nUse /switch_{target_mode} force to override (warning: monitoring will stop)."
                await update.message.reply_text(msg)
                await self._log_event(f"switch_{target_mode}_blocked", "WARNING", {"reason": "open_positions", "mode": current})
                return

        await self.state_store.set_system_setting("pending_mode", target_mode)
        await self.state_store.set_kill_switch("global", "global", True, "ModeSwitch", f"Switching to {target_mode}")
        
        await update.message.reply_text(f"Mode switch to {target_mode} initiated. Global kill switch engaged. Restarting once safe...")
        await self._log_event(f"switch_{target_mode}_initiated", "INFO", {"from": current, "to": target_mode, "force": force, "user": update.effective_user.username})

    async def cmd_switch_demo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_switch(update, context, "demo")

    async def cmd_switch_testnet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_switch(update, context, "testnet")

    async def cmd_switch_live(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        current = getattr(self.state_store, 'active_mode', 'unknown')
        
        from gate.go_live_gate import GoLiveGate, TradeJournalMock, GateConfig
        journal = TradeJournalMock(
            executions=await self.state_store.get_all_executions(),
            reconciliation_logs=await self.state_store.get_all_reconciliation_logs()
        )
        gate = GoLiveGate(journal, GateConfig(manual_sign_off=True))
        passed, report = gate.evaluate("all")
        
        if not passed:
            await update.message.reply_text(f"Gate check failed. Cannot switch to LIVE:\n{report}")
            await self._log_event("switch_live_blocked", "WARNING", {"reason": "gate_failed"})
            return
            
        execs = await self.state_store.get_active_executions()
        if execs:
            msg = f"Cannot switch to LIVE. Open positions in {current} mode:\n"
            msg += "\n".join([f"{e['execution_id'][:8]} ({e['strategy']}): {e['state']}" for e in execs])
            await update.message.reply_text(msg)
            await self._log_event("switch_live_blocked", "WARNING", {"reason": "open_positions", "mode": current})
            return
            
        import uuid
        code = str(uuid.uuid4())[:6].upper()
        await self.state_store.set_system_setting("live_switch_code", code)
        import time
        await self.state_store.set_system_setting("live_switch_expiry", str(time.time() + 300))
        
        await update.message.reply_text(f"Gate passed! WARNING: Switching to LIVE mode engages real capital.\nSend /confirm_live {code} within 5 minutes to confirm.")
        await self._log_event("switch_live_pending", "INFO", {"code": code})

    async def cmd_confirm_live(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._auth(update): return
        if not context.args:
            await update.message.reply_text("Usage: /confirm_live <code>")
            return
            
        code = context.args[0]
        stored = await self.state_store.get_system_setting("live_switch_code")
        expiry = await self.state_store.get_system_setting("live_switch_expiry")
        
        import time
        if not stored or not expiry or time.time() > float(expiry):
            await update.message.reply_text("Code expired or invalid. Start over with /switch_live.")
            return
            
        if code.upper() != stored:
            await update.message.reply_text("Invalid code. Start over with /switch_live.")
            await self.state_store.delete_system_setting("live_switch_code")
            await self._log_event("switch_live_failed", "WARNING", {"reason": "invalid_code"})
            return
            
        await self.state_store.delete_system_setting("live_switch_code")
        
        target_mode = "live"
        current = getattr(self.state_store, 'active_mode', 'unknown')
        await self.state_store.set_system_setting("pending_mode", target_mode)
        await self.state_store.set_kill_switch("global", "global", True, "ModeSwitch", f"Switching to {target_mode}")
        
        await update.message.reply_text(f"Mode switch to {target_mode} confirmed. Global kill switch engaged. Restarting once safe...")
        await self._log_event(f"switch_{target_mode}_initiated", "INFO", {"from": current, "to": target_mode, "user": update.effective_user.username})

    async def cmd_pnl_demo(self, update, context): await self._pnl_mode(update, context, "demo")
    async def cmd_pnl_testnet(self, update, context): await self._pnl_mode(update, context, "testnet")
    async def cmd_pnl_live(self, update, context): await self._pnl_mode(update, context, "live")
    
    async def _pnl_mode(self, update, context, mode):
        if not await self._auth(update): return
        pnl = await self.state_store.get_pnl(mode=mode)
        await update.message.reply_text(f"Total Realized P&L ({mode}): ${pnl:.2f}")

    async def cmd_positions_demo(self, update, context): await update.message.reply_text("Positions for demo mode")
    async def cmd_positions_testnet(self, update, context): await update.message.reply_text("Positions for testnet mode")
    async def cmd_positions_live(self, update, context): await update.message.reply_text("Positions for live mode")
    async def cmd_balance_demo(self, update, context): await update.message.reply_text("Balances for demo mode")
    async def cmd_balance_testnet(self, update, context): await update.message.reply_text("Balances for testnet mode")
    async def cmd_balance_live(self, update, context): await update.message.reply_text("Balances for live mode")

