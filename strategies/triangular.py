from typing import Dict, Any, List, Tuple
from core.exchange_client import ExchangeClient
from core.fee_calculator import FeeCalculator
from core.orderbook_manager import OrderBookManager
from core.execution_engine import ExecutionStateMachine, ExecutionState, ExecutionContext
from core.logger import logging
import asyncio

logger = logging.getLogger("TriangularStrategy")
logger.setLevel(logging.INFO)

class TriangularArbitrageStrategy:
    """
    Strategy A: Triangular Arbitrage (Section 3).
    """
    def __init__(
        self,
        exchange_client: ExchangeClient,
        fee_calculator: FeeCalculator,
        orderbook_manager: OrderBookManager,
        state_machine: ExecutionStateMachine,
        config: Dict[str, Any]
    ):
        self.client = exchange_client
        self.fee_calc = fee_calculator
        self.orderbook_manager = orderbook_manager
        self.state_machine = state_machine
        self.exchange_name = config.get('exchange', 'binance')
        
        self.min_profit_threshold_pct = config.get('min_profit_threshold_pct', 0.15)
        self.max_position_size_usd = config.get('max_position_size_usd', 200.0)
        self.slippage_buffer_pct = config.get('slippage_buffer_pct', 0.05)
        self.partial_fill_min_pct = config.get('partial_fill_min_viable_pct', 50.0)
        
        # We will assume fixed config for MVP: USDT -> BTC -> ETH -> USDT
        # This translates to:
        # Leg 1: Buy BTC/USDT using USDT
        # Leg 2: Buy ETH/BTC using BTC  (or Sell BTC/ETH, depending on quote asset)
        # Leg 3: Sell ETH/USDT to get USDT back

    async def evaluate_triangle(self, triangle_def: List[Dict[str, str]], base_investment: float) -> Dict[str, Any]:
        """
        Evaluates a triangle using the comprehensive fee calculator.
        triangle_def is a list of 3 legs, e.g.:
        [{'symbol': 'BTCUSDT', 'side': 'buy'}, {'symbol': 'ETHBTC', 'side': 'buy'}, {'symbol': 'ETHUSDT', 'side': 'sell'}]
        """
        legs = []
        current_qty = base_investment
        
        try:
            for leg_def in triangle_def:
                symbol = leg_def['symbol']
                side = leg_def['side']
                
                book = await self.orderbook_manager.get_book(self.exchange_name, symbol)
                if not book:
                    return {"is_viable": False, "reason": f"No order book for {symbol}"}
                
                # To accurately size the next leg, we need to know the price of this leg.
                # Since fee_calc walks the book independently, we estimate price here for sizing.
                top_ask = book.asks[0][0] if book.asks else 0
                top_bid = book.bids[0][0] if book.bids else 0
                price = top_ask if side == 'buy' else top_bid
                
                if price <= 0:
                    return {"is_viable": False, "reason": f"Invalid price for {symbol}"}
                
                # Determine order size for the base asset of this pair
                # E.g. Buy BTC/USDT with 200 USDT -> qty = 200 / price
                # Sell ETH/USDT -> qty = current ETH holding
                if side == 'buy':
                    size = current_qty / price
                else:
                    size = current_qty
                    
                legs.append({
                    'exchange': self.exchange_name,
                    'symbol': symbol,
                    'side': side,
                    'size': size,
                    'order_book': book
                })
                
                # Update current_qty for the next leg's starting capital
                if side == 'buy':
                    current_qty = size # We now have the base asset
                else:
                    current_qty = size * price # We now have the quote asset
                    
            # Calculate profitability
            # The min profit is a percentage of investment, converting it to absolute
            min_profit_abs = base_investment * (self.min_profit_threshold_pct / 100.0)
            
            explicit_gross_pnl = current_qty - base_investment
            
            result = await self.fee_calc.calculate_net_profit(
                strategy="triangular",
                legs=legs,
                slippage_buffer_pct=self.slippage_buffer_pct,
                latency_decay_estimate_pct=0.01,
                min_profit_threshold=min_profit_abs,
                explicit_gross_pnl=explicit_gross_pnl
            )

            net_profit_est = result.get("net_profit", 0.0)
            
            # Check against config threshold
            is_viable = net_profit_est > min_profit_abs
            
            gross_spread_pct = (explicit_gross_pnl / base_investment) * 100.0 if base_investment > 0 else 0.0
            
            # Log opportunity to DB per Section 10
            opp_data = {
                "strategy": "triangular",
                "symbols": "-".join([d['symbol'] for d in triangle_def]),
                "gross_spread_pct": gross_spread_pct,
                "net_profit_estimate": net_profit_est,
                "fee_breakdown": result.get("fee_breakdown", {}),
                "threshold_at_time": self.min_profit_threshold_pct,
                "action_taken": "EXECUTE" if is_viable else "REJECTED",
                "execution_id": None
            }
            await self.state_machine.state_store.save_opportunity(opp_data)
            
            # Pack legs into result so execution engine can use them
            result['legs'] = legs
            result['is_viable'] = is_viable
            return result
            
        except ValueError as e:
            return {"is_viable": False, "reason": f"FeeCalc Error: {e}"}

    async def execute_triangle(self, context: ExecutionContext, legs: List[Dict[str, Any]]):
        """
        Executes the triangle, updating state machine and handling partial fills per Section 3.4
        """
        await self.state_machine.transition(context, ExecutionState.VALIDATING)
        # Assuming still valid, proceed
        
        filled_legs = []
        
        # State mapping
        exec_states = [ExecutionState.EXECUTING_LEG_1, ExecutionState.EXECUTING_LEG_2, ExecutionState.EXECUTING_LEG_3]
        
        try:
            for idx, leg in enumerate(legs):
                await self.state_machine.transition(context, exec_states[idx])
                
                target_size = leg['size']
                # If subsequent leg, adjust target size based on previous actual fill if needed
                if idx > 0:
                    prev_leg = filled_legs[-1]
                    prev_filled_qty = prev_leg['filled_qty']
                    if prev_leg['side'] == 'buy':
                        target_size = prev_filled_qty # We bought X base, can only use X for next step
                
                order = await self.client.place_order(
                    symbol=leg['symbol'],
                    side=leg['side'],
                    order_type="market",
                    quantity=target_size
                )
                
                filled_qty = order.get("filled_qty", 0.0)
                order['intended_size'] = target_size
                filled_legs.append(order)
                
                # Log execution leg to DB
                leg_data = {
                    "execution_id": context.execution_id,
                    "leg_number": idx + 1,
                    "exchange": self.exchange_name,
                    "symbol": leg['symbol'],
                    "side": leg['side'],
                    "intended_qty": target_size,
                    "filled_qty": filled_qty,
                    "avg_fill_price": order.get("price", 0.0),
                    "fee_paid": order.get("fee_paid", 0.0),
                    "order_id": order.get("order_id", "unknown"),
                    "status": "FILLED" if filled_qty >= target_size else "PARTIAL",
                }
                await self.state_machine.state_store.save_execution_leg(leg_data)
                
                # Partial Fill logic (Section 3.4)
                if filled_qty < target_size:
                    fill_pct = (filled_qty / target_size) * 100.0
                    
                    if fill_pct < self.partial_fill_min_pct:
                        logger.warning(f"Leg {idx+1} partial fill ({fill_pct:.1f}%) below min viable limit. Unwinding.")
                        await self.state_machine.transition(context, ExecutionState.PARTIAL_FAILURE)
                        await self._unwind_legs(context, filled_legs)
                        return
                    else:
                        logger.info(f"Leg {idx+1} partial fill ({fill_pct:.1f}%). Adjusting next leg.")
                        # Next iteration will automatically adjust size based on this filled_qty
                        
            await self.state_machine.transition(context, ExecutionState.CONFIRMING_FILLS)
            # Re-check statuses in reality...
            
            await self.state_machine.transition(context, ExecutionState.COMPLETED, data_updates={"filled_legs": filled_legs})
            
        except Exception as e:
            logger.error(f"Execution failed on leg. Unwinding. Error: {e}")
            await self.state_machine.transition(context, ExecutionState.PARTIAL_FAILURE)
            await self._unwind_legs(context, filled_legs)

    async def _unwind_legs(self, context: ExecutionContext, filled_legs: List[Dict[str, Any]]):
        """
        Unwinds previously filled legs in reverse order via market orders (Section 3.4).
        """
        await self.state_machine.transition(context, ExecutionState.UNWINDING)
        
        try:
            # Reverse order
            for order in reversed(filled_legs):
                filled_qty = order['filled_qty']
                if filled_qty <= 0:
                    continue
                    
                reverse_side = "sell" if order['side'] == "buy" else "buy"
                logger.info(f"Unwinding: {reverse_side} {filled_qty} {order['symbol']}")
                
                await self.client.place_order(
                    symbol=order['symbol'],
                    side=reverse_side,
                    order_type="market",
                    quantity=filled_qty
                )
                
            await self.state_machine.transition(context, ExecutionState.UNWOUND)
            
        except Exception as e:
            logger.error(f"Failed to unwind cleanly: {e}")
            await self.state_machine.transition(context, ExecutionState.STUCK, data_updates={"unwind_error": str(e)})

