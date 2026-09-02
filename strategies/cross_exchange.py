import asyncio
from typing import Dict, Any, List
from core.exchange_client import ExchangeClient
from core.fee_calculator import FeeCalculator
from core.orderbook_manager import OrderBookManager
from core.execution_engine import ExecutionStateMachine, ExecutionState, ExecutionContext
from core.inventory_manager import CrossExchangeInventoryManager
from core.logger import logging

logger = logging.getLogger("CrossExchangeStrategy")
logger.setLevel(logging.INFO)

class CrossExchangeArbitrageStrategy:
    """
    Strategy B: Cross-Exchange Arbitrage (Section 4).
    """
    def __init__(
        self,
        exchange_clients: Dict[str, ExchangeClient],
        fee_calculator: FeeCalculator,
        orderbook_manager: OrderBookManager,
        state_machine: ExecutionStateMachine,
        inventory_manager: CrossExchangeInventoryManager,
        config: Dict[str, Any]
    ):
        self.clients = exchange_clients
        self.fee_calc = fee_calculator
        self.orderbook_manager = orderbook_manager
        self.state_machine = state_machine
        self.inventory_manager = inventory_manager
        self.config = config
        
        self.min_profit_threshold_pct = config.get('min_profit_threshold_pct', 0.25)
        self.max_position_size_usd = config.get('max_position_size_usd', 300.0)
        self.withdrawal_fee_usd = config.get('withdrawal_fee_usd', 5.0)

    async def evaluate_opportunity(self, symbol: str, buy_exchange: str, sell_exchange: str, size: float) -> Dict[str, Any]:
        buy_book = await self.orderbook_manager.get_book(buy_exchange, symbol)
        sell_book = await self.orderbook_manager.get_book(sell_exchange, symbol)
        
        if not buy_book or not sell_book:
            return {"is_viable": False, "reason": "Missing order books"}

        legs = [
            {'exchange': buy_exchange, 'symbol': symbol, 'side': 'buy', 'size': size, 'order_book': buy_book},
            {'exchange': sell_exchange, 'symbol': symbol, 'side': 'sell', 'size': size, 'order_book': sell_book}
        ]
        
        # Gross pnl isn't chained natively in fee calc for parallel legs very well unless we explicitly set it
        # Actually, standard fee calculator walks the book and correctly adds/subtracts notional
        # if both legs are same size.
        
        # Calculate profitability dynamically
        avg_price = buy_book.asks[0][0] if buy_book.asks else 0.0
        min_profit_abs = (size * avg_price) * (self.min_profit_threshold_pct / 100.0)
        
        try:
            result = await self.fee_calc.calculate_net_profit(
                strategy="cross_exchange",
                legs=legs,
                slippage_buffer_pct=0.05,
                latency_decay_estimate_pct=0.01,
                min_profit_threshold=min_profit_abs,
                cross_exchange_withdrawal_fee=self.withdrawal_fee_usd
            )
            result['legs'] = legs
            return result
        except ValueError as e:
            return {"is_viable": False, "reason": f"FeeCalc Error: {e}"}

    async def execute_arbitrage(self, context: ExecutionContext, legs: List[Dict[str, Any]]):
        """
        Executes both legs in parallel. If one fails, unwinds the filled one.
        """
        await self.state_machine.transition(context, ExecutionState.VALIDATING)
        
        # Move both to parallel execution conceptually, but state machine is linear.
        # We will represent this by transitioning to EXECUTING_LEG_1 (which means "executing all parallel legs" here)
        await self.state_machine.transition(context, ExecutionState.EXECUTING_LEG_1)
        
        buy_leg = legs[0]
        sell_leg = legs[1]
        
        # Fire in parallel!
        results = await asyncio.gather(
            self.clients[buy_leg['exchange']].place_order(
                symbol=buy_leg['symbol'], side='buy', order_type='market', quantity=buy_leg['size']
            ),
            self.clients[sell_leg['exchange']].place_order(
                symbol=sell_leg['symbol'], side='sell', order_type='market', quantity=sell_leg['size']
            ),
            return_exceptions=True
        )
        
        buy_res, sell_res = results
        
        buy_success = not isinstance(buy_res, Exception) and buy_res.get('filled_qty', 0) > 0
        sell_success = not isinstance(sell_res, Exception) and sell_res.get('filled_qty', 0) > 0
        
        filled_legs = []
        if buy_success:
            buy_res['exchange'] = buy_leg['exchange']
            buy_res['side'] = 'buy'
            filled_legs.append(buy_res)
        if sell_success:
            sell_res['exchange'] = sell_leg['exchange']
            sell_res['side'] = 'sell'
            filled_legs.append(sell_res)
            
        if buy_success and sell_success:
            # Both filled! 
            await self.state_machine.transition(context, ExecutionState.COMPLETED, data_updates={"filled_legs": filled_legs})
            
            # Route resulting balance changes through Inventory Manager
            exchanges = [buy_leg['exchange'], sell_leg['exchange']]
            base_asset = buy_leg['symbol'].replace("USDT", "")
            await self.inventory_manager.check_skew(exchanges, base_asset)
            
        else:
            # Partial failure, we need to unwind whatever filled
            logger.error("Parallel execution partial failure. Initiating unwind.")
            await self.state_machine.transition(context, ExecutionState.PARTIAL_FAILURE)
            await self._unwind_legs(context, filled_legs)

    async def _unwind_legs(self, context: ExecutionContext, filled_legs: List[Dict[str, Any]]):
        """
        Unwinds the successful leg if the other failed.
        """
        await self.state_machine.transition(context, ExecutionState.UNWINDING)
        
        try:
            for order in filled_legs:
                filled_qty = order.get('filled_qty', 0)
                if filled_qty <= 0:
                    continue
                    
                reverse_side = "sell" if order['side'] == "buy" else "buy"
                exchange = order['exchange']
                symbol = order.get('symbol', context.data.get('symbol', 'BTCUSDT')) # Fallback if symbol missing in result
                
                logger.info(f"Unwinding parallel leg: {reverse_side} {filled_qty} {symbol} on {exchange}")
                
                client = self.clients[exchange]
                
                await client.place_order(
                    symbol=symbol,
                    side=reverse_side,
                    order_type="market",
                    quantity=filled_qty
                )
                
            await self.state_machine.transition(context, ExecutionState.UNWOUND)
            
        except Exception as e:
            logger.error(f"Failed to unwind cleanly: {e}")
            await self.state_machine.transition(context, ExecutionState.STUCK, data_updates={"unwind_error": str(e)})

