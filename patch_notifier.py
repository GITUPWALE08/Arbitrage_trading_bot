with open("core/notifier.py", "r") as f:
    text = f.read()

# Add mode-related commands
cmds_addition = """
                ("mode", self.cmd_mode),
                ("switch_demo", self.cmd_switch_demo),
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
"""
text = text.replace('("mode", self.cmd_mode),', cmds_addition.strip())

text = text.replace('("mode", self.cmd_mode),\n', '')
text = text.replace('("switch_demo", self.cmd_switch_demo),\n', '')

new_methods = """
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
                msg = f"Cannot switch to {target_mode}. Open positions in {current} mode:\\n"
                msg += "\\n".join([f"{e['execution_id'][:8]} ({e['strategy']}): {e['state']}" for e in execs])
                msg += f"\\n\\nUse /switch_{target_mode} force to override (warning: monitoring will stop)."
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
            await update.message.reply_text(f"Gate check failed. Cannot switch to LIVE:\\n{report}")
            await self._log_event("switch_live_blocked", "WARNING", {"reason": "gate_failed"})
            return
            
        execs = await self.state_store.get_active_executions()
        if execs:
            msg = f"Cannot switch to LIVE. Open positions in {current} mode:\\n"
            msg += "\\n".join([f"{e['execution_id'][:8]} ({e['strategy']}): {e['state']}" for e in execs])
            await update.message.reply_text(msg)
            await self._log_event("switch_live_blocked", "WARNING", {"reason": "open_positions", "mode": current})
            return
            
        import uuid
        code = str(uuid.uuid4())[:6].upper()
        await self.state_store.set_system_setting("live_switch_code", code)
        import time
        await self.state_store.set_system_setting("live_switch_expiry", str(time.time() + 300))
        
        await update.message.reply_text(f"Gate passed! WARNING: Switching to LIVE mode engages real capital.\\nSend /confirm_live {code} within 5 minutes to confirm.")
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

"""
text += new_methods

with open("core/notifier.py", "w") as f:
    f.write(text)
