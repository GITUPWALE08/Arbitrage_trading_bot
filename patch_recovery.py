import os

def patch_main():
    with open('main.py', 'r') as f:
        content = f.read()
    
    if "CRASH RECOVERY" in content:
        return

    recovery_code = """
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

    report_str = "\\n".join(recovery_report)
    logger.info(f"Crash Recovery Report:\\n{report_str}")
    await notifier.send_high_priority_alert(f"Bot Started. Recovery Report:\\n{report_str}")

"""
    
    target = "# 6. Start Async Background Tasks"
    new_content = content.replace(target, recovery_code + "\n    " + target)
    
    with open('main.py', 'w') as f:
        f.write(new_content)

if __name__ == "__main__":
    patch_main()
