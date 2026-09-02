with open("main.py", "r") as f:
    text = f.read()

# 1. Update main_trading_loop
loop_addition = """
        # Check for pending mode switch
        pending_mode = await state_store.get_system_setting("pending_mode")
        if pending_mode:
            execs = await state_store.get_active_executions()
            if not execs:
                logger.info(f"Safe to switch to {pending_mode}. Triggering restart.")
                os._exit(0)
"""
text = text.replace("        # 1. Check Global Risk Switches", loop_addition + "\n        # 1. Check Global Risk Switches")
text = text.replace("    fast_store: any", "    fast_store: any,\n    state_store=None")

# 2. Update run_bot initialization
init_addition = """
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
"""
text = text.replace("    await state_store.initialize_db()", "    await state_store.initialize_db()\n" + init_addition)

# 3. Update CCXT logic
old_ccxt = """    # Check if we are in live mode
    golive_active = os.path.exists("GOLIVE_APPROVED.flag")"""

new_ccxt = """    # Check if we are in live mode based on active_mode
    golive_active = (active_mode == "live")"""
text = text.replace(old_ccxt, new_ccxt)

# 4. Handle demo/testnet
old_ccxt_init = """    if golive_active:
        logger.warning("🔴 LIVE TRADING MODE ENGAGED. Using CCXTExchangeClient.")
        from core.ccxt_client import CCXTExchangeClient
        binance_key = os.getenv("BINANCE_API_KEY", "")
        binance_sec = os.getenv("BINANCE_SECRET", "")
        kraken_key = os.getenv("KRAKEN_API_KEY", "")
        kraken_sec = os.getenv("KRAKEN_SECRET", "")
        
        client_binance = CCXTExchangeClient("binance", binance_key, binance_sec, testnet=False)
        client_kraken = CCXTExchangeClient("kraken", kraken_key, kraken_sec, testnet=False)"""

new_ccxt_init = """    if active_mode in ["live", "testnet", "demo"]:
        logger.warning(f"🔴 {active_mode.upper()} TRADING MODE ENGAGED. Using CCXTExchangeClient.")
        from core.ccxt_client import CCXTExchangeClient
        binance_key = os.getenv("BINANCE_API_KEY", "")
        binance_sec = os.getenv("BINANCE_SECRET", "")
        kraken_key = os.getenv("KRAKEN_API_KEY", "")
        kraken_sec = os.getenv("KRAKEN_SECRET", "")
        
        is_testnet = (active_mode == "testnet")
        client_binance = CCXTExchangeClient("binance", binance_key, binance_sec, testnet=is_testnet)
        client_kraken = CCXTExchangeClient("kraken", kraken_key, kraken_sec, testnet=is_testnet)"""
text = text.replace(old_ccxt_init, new_ccxt_init)

# 5. Fix main_trading_loop call
text = text.replace("main_trading_loop(strategies, risk_manager, gate, fast_store)", "main_trading_loop(strategies, risk_manager, gate, fast_store, state_store)")

with open("main.py", "w") as f:
    f.write(text)
