import time
import asyncio
from typing import Dict, Any, List, Optional
from core.exchange_client import ExchangeClient
from core.fee_calculator import FeeCalculator
from core.execution_engine import ExecutionStateMachine, ExecutionContext, ExecutionState
from core.logger import logging

logger = logging.getLogger("FundingRateStrategy")
logger.setLevel(logging.INFO)

class FundingRateStrategy:
    def __init__(
        self,
        exchange_client: ExchangeClient,
        fee_calculator: FeeCalculator,
        config: Dict[str, Any],
        state_store=None,
        state_machine: ExecutionStateMachine = None
    ):
        self.client = exchange_client
        self.fee_calc = fee_calculator
        self.config = config
        self.state_store = state_store
        self.state_machine = state_machine
        
        # Parse config with spec Section 5.5 defaults
        self.window_days = config.get('funding_history_window_days', 10)
        self.min_annualized_pct = config.get('min_trailing_annualized_funding_pct', 10.0)
        self.max_negative_flips = config.get('max_negative_flips_in_window', 2)
        self.max_basis_pct = config.get('max_basis_pct', 0.5)
        self.min_holding_hr = config.get('min_holding_period_hr', 24)
        self.max_holding_days = config.get('max_holding_period_days', 14)
        self.position_size_usd = config.get('position_size_usd', 500.0)
        
        # Track active positions in-memory (persisted to DB via state machine)
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        
    def _annualize_rate(self, rate: float, interval_hours: int = 8) -> float:
        """Convert a single funding period rate to an annualized percentage."""
        periods_per_year = (24 / interval_hours) * 365
        return rate * periods_per_year * 100.0

    @property
    def exchange_name(self) -> str:
        """Get exchange name compatible with both Simulator and CCXT clients."""
        return getattr(self.client, 'exchange_name', getattr(self.client, 'exchange_id', 'unknown'))

    async def _analyze_funding_history(self, symbol: str) -> Dict[str, Any]:
        """
        Pulls historical window and calculates trailing average and stability.
        """
        if not hasattr(self, '_funding_cache'):
            self._funding_cache = {}
            self._funding_cache_time = {}
            
        now = time.time()
        if symbol in self._funding_cache and (now - self._funding_cache_time.get(symbol, 0)) < 300: # 5 minute cache
            history = self._funding_cache[symbol]
        else:
            history = await self.client.get_historical_funding_rates(symbol, self.window_days)
            if history:
                self._funding_cache[symbol] = history
                self._funding_cache_time[symbol] = now
            else:
                # Cache empty result for 60s to prevent API flooding
                self._funding_cache[symbol] = []
                self._funding_cache_time[symbol] = now - 240  # Retry in 60s, not 300s

        if not history:
            return {"viable": False, "reason": "No funding history available"}
            
        negative_flips = 0
        total_annualized = 0.0
        
        for record in history:
            rate = record['rate']
            if rate < 0:
                negative_flips += 1
            ann_pct = self._annualize_rate(rate)
            total_annualized += ann_pct
            
        avg_annualized = total_annualized / len(history)
        
        if negative_flips > self.max_negative_flips:
            return {"viable": False, "reason": f"Too many negative flips ({negative_flips})"}
            
        if avg_annualized < self.min_annualized_pct:
            return {"viable": False, "reason": f"Trailing average ({avg_annualized:.2f}%) below threshold"}
            
        return {
            "viable": True,
            "avg_annualized_pct": avg_annualized,
            "negative_flips": negative_flips
        }

    def _estimate_net_profit(self, avg_annualized_pct: float, spot_price: float) -> Dict[str, Any]:
        """
        Spec Section 5.3 Step 4: Amortize entry+exit trading costs over expected holding period.
        """
        # Estimate holding period (conservative: min holding period)
        holding_days = max(self.min_holding_hr / 24.0, 7.0)  # At least 7 days for meaningful yield
        
        # Calculate expected gross funding yield over holding period
        daily_yield_pct = avg_annualized_pct / 365.0
        gross_yield_pct = daily_yield_pct * holding_days
        gross_yield_usd = self.position_size_usd * (gross_yield_pct / 100.0)
        
        # Entry costs: spot taker fee + perp taker fee (both legs)
        fee_rate = 0.001  # 0.1% taker fee (conservative default)
        entry_fees = self.position_size_usd * fee_rate * 2  # Both spot buy + perp sell
        exit_fees = self.position_size_usd * fee_rate * 2   # Both spot sell + perp buy
        total_fees = entry_fees + exit_fees
        
        net_profit_usd = gross_yield_usd - total_fees
        
        return {
            "gross_yield_usd": gross_yield_usd,
            "gross_yield_pct": gross_yield_pct,
            "total_fees_usd": total_fees,
            "net_profit_usd": net_profit_usd,
            "net_profit_pct": (net_profit_usd / self.position_size_usd) * 100.0 if self.position_size_usd > 0 else 0.0,
            "estimated_holding_days": holding_days,
            "fee_breakdown": {
                "entry_fees": entry_fees,
                "exit_fees": exit_fees,
            }
        }

    async def evaluate_entry(self, spot_symbol: str, perp_symbol: str) -> Dict[str, Any]:
        """
        Evaluates if we should enter a cash-and-carry position.
        1. Check funding history stability/average.
        2. Check basis risk (spot vs futures gap).
        3. Amortize entry+exit costs (Spec 5.3 Step 4).
        """
        # Don't enter if we already have an active position for this pair
        position_key = f"{spot_symbol}-{perp_symbol}"
        if position_key in self.active_positions:
            return {"enter": False, "reason": "Already in active position"}
        
        funding_analysis = await self._analyze_funding_history(perp_symbol)
        if not funding_analysis['viable']:
            if self.state_store:
                await self.state_store.save_opportunity({
                    "strategy": "funding_rate",
                    "symbols": position_key,
                    "gross_spread_pct": 0.0,
                    "net_profit_estimate": 0.0,
                    "fee_breakdown": {},
                    "threshold_at_time": self.min_annualized_pct,
                    "action_taken": f"REJECTED: {funding_analysis['reason']}",
                    "execution_id": None
                })
            return {"enter": False, "reason": funding_analysis['reason']}
            
        # Check basis
        spot_price = await self.client.get_mark_price(spot_symbol)
        perp_price = await self.client.get_mark_price(perp_symbol)
        
        if spot_price <= 0:
            return {"enter": False, "reason": "Invalid spot price"}
            
        basis_pct = abs((perp_price - spot_price) / spot_price) * 100.0
        
        if basis_pct > self.max_basis_pct:
            if self.state_store:
                await self.state_store.save_opportunity({
                    "strategy": "funding_rate",
                    "symbols": position_key,
                    "gross_spread_pct": funding_analysis['avg_annualized_pct'],
                    "net_profit_estimate": 0.0,
                    "fee_breakdown": {},
                    "threshold_at_time": self.min_annualized_pct,
                    "action_taken": f"REJECTED: Basis ({basis_pct:.2f}%) exceeds max ({self.max_basis_pct}%)",
                    "execution_id": None
                })
            return {"enter": False, "reason": f"Basis ({basis_pct:.2f}%) exceeds max allowed ({self.max_basis_pct}%)"}
        
        # Amortize entry/exit costs (Spec 5.3 Step 4)
        profit_est = self._estimate_net_profit(funding_analysis['avg_annualized_pct'], spot_price)
        
        if profit_est['net_profit_usd'] <= 0:
            if self.state_store:
                await self.state_store.save_opportunity({
                    "strategy": "funding_rate",
                    "symbols": position_key,
                    "gross_spread_pct": funding_analysis['avg_annualized_pct'],
                    "net_profit_estimate": profit_est['net_profit_usd'],
                    "fee_breakdown": profit_est['fee_breakdown'],
                    "threshold_at_time": self.min_annualized_pct,
                    "action_taken": f"REJECTED: Negative net after fees (${profit_est['net_profit_usd']:.2f})",
                    "execution_id": None
                })
            return {"enter": False, "reason": f"Not profitable after fees: ${profit_est['net_profit_usd']:.2f}"}
            
        result = {
            "enter": True,
            "avg_annualized_pct": funding_analysis['avg_annualized_pct'],
            "basis_pct": basis_pct,
            "spot_price": spot_price,
            "perp_price": perp_price,
            "net_profit_estimate": profit_est['net_profit_usd'],
            "fee_breakdown": profit_est['fee_breakdown']
        }
        
        if self.state_store:
            await self.state_store.save_opportunity({
                "strategy": "funding_rate",
                "symbols": position_key,
                "gross_spread_pct": funding_analysis['avg_annualized_pct'],
                "net_profit_estimate": profit_est['net_profit_usd'],
                "fee_breakdown": profit_est['fee_breakdown'],
                "threshold_at_time": self.min_annualized_pct,
                "action_taken": "EXECUTE",
                "execution_id": None
            })
            
        return result

    async def execute_entry(self, context: ExecutionContext, spot_symbol: str, perp_symbol: str, spot_price: float):
        """
        Executes cash-and-carry entry: buy spot + short perp in parallel.
        Runs through the state machine per Spec Section 2.1 and 5.3.
        """
        await self.state_machine.transition(context, ExecutionState.VALIDATING)
        
        # Calculate position sizes
        spot_qty = self.position_size_usd / spot_price
        perp_qty = spot_qty  # Equal-notional
        
        await self.state_machine.transition(context, ExecutionState.EXECUTING_LEG_1)
        
        # Fire both legs in parallel (spot buy + perp sell)
        results = await asyncio.gather(
            self.client.place_order(
                symbol=spot_symbol, side='buy', order_type='market', quantity=spot_qty
            ),
            self.client.place_order(
                symbol=perp_symbol, side='sell', order_type='market', quantity=perp_qty
            ),
            return_exceptions=True
        )
        
        spot_res, perp_res = results
        
        spot_filled = not isinstance(spot_res, Exception) and spot_res.get('filled_qty', spot_res.get('filled', 0)) > 0
        perp_filled = not isinstance(perp_res, Exception) and perp_res.get('filled_qty', perp_res.get('filled', 0)) > 0
        
        if spot_filled and perp_filled:
            position_key = f"{spot_symbol}-{perp_symbol}"
            self.active_positions[position_key] = {
                "entry_time": time.time(),
                "execution_id": context.execution_id,
                "spot_symbol": spot_symbol,
                "perp_symbol": perp_symbol,
                "spot_qty": spot_res.get('filled_qty', spot_res.get('filled', 0)),
                "perp_qty": perp_res.get('filled_qty', perp_res.get('filled', 0)),
                "spot_entry_price": spot_res.get('average_price', spot_res.get('price', spot_price)),
                "perp_entry_price": perp_res.get('average_price', perp_res.get('price', spot_price)),
            }
            
            await self.state_machine.transition(context, ExecutionState.COMPLETED, data_updates={
                "position": self.active_positions[position_key],
                "spot_order": {k: v for k, v in spot_res.items() if not isinstance(v, (list, dict))} if isinstance(spot_res, dict) else {},
                "perp_order": {k: v for k, v in perp_res.items() if not isinstance(v, (list, dict))} if isinstance(perp_res, dict) else {},
            })
            logger.info(f"Funding rate entry COMPLETED for {position_key}. Spot: {spot_res.get('filled_qty', spot_res.get('filled', 0))}, Perp: {perp_res.get('filled_qty', perp_res.get('filled', 0))}")
        else:
            # Partial failure — unwind whatever filled
            logger.error(f"Funding rate entry partial failure. Spot filled: {spot_filled}, Perp filled: {perp_filled}")
            await self.state_machine.transition(context, ExecutionState.PARTIAL_FAILURE)
            await self._unwind_entry(context, spot_symbol, perp_symbol, spot_res if spot_filled else None, perp_res if perp_filled else None)

    async def execute_exit(self, context: ExecutionContext, position_key: str):
        """
        Executes cash-and-carry exit: sell spot + buy perp to close.
        """
        position = self.active_positions.get(position_key)
        if not position:
            logger.error(f"No active position found for {position_key}")
            return
        
        await self.state_machine.transition(context, ExecutionState.VALIDATING)
        await self.state_machine.transition(context, ExecutionState.EXECUTING_LEG_1)
        
        results = await asyncio.gather(
            self.client.place_order(
                symbol=position['spot_symbol'], side='sell', order_type='market', quantity=position['spot_qty']
            ),
            self.client.place_order(
                symbol=position['perp_symbol'], side='buy', order_type='market', quantity=position['perp_qty']
            ),
            return_exceptions=True
        )
        
        spot_res, perp_res = results
        
        spot_closed = not isinstance(spot_res, Exception) and spot_res.get('filled_qty', spot_res.get('filled', 0)) > 0
        perp_closed = not isinstance(perp_res, Exception) and perp_res.get('filled_qty', perp_res.get('filled', 0)) > 0
        
        if spot_closed and perp_closed:
            # Calculate realized P&L
            exit_spot_price = spot_res.get('average_price', spot_res.get('price', 0))
            exit_perp_price = perp_res.get('average_price', perp_res.get('price', 0))
            
            spot_pnl = (exit_spot_price - position['spot_entry_price']) * position['spot_qty']
            perp_pnl = (position['perp_entry_price'] - exit_perp_price) * position['perp_qty']  # Short: entry - exit
            realized_profit = spot_pnl + perp_pnl
            
            await self.state_machine.transition(context, ExecutionState.COMPLETED, data_updates={
                "realized_profit": realized_profit,
                "spot_pnl": spot_pnl,
                "perp_pnl": perp_pnl,
                "holding_hours": (time.time() - position['entry_time']) / 3600.0
            })
            
            del self.active_positions[position_key]
            logger.info(f"Funding rate exit COMPLETED for {position_key}. Realized P&L: ${realized_profit:.4f}")
        else:
            logger.error(f"Funding rate exit partial failure. Spot closed: {spot_closed}, Perp closed: {perp_closed}")
            await self.state_machine.transition(context, ExecutionState.PARTIAL_FAILURE)
            await self.state_machine.transition(context, ExecutionState.STUCK, data_updates={
                "reason": "Exit partial failure — manual intervention required",
                "position": position
            })

    async def _unwind_entry(self, context, spot_symbol, perp_symbol, spot_res, perp_res):
        """Unwind a partially-filled entry."""
        await self.state_machine.transition(context, ExecutionState.UNWINDING)
        try:
            if spot_res:
                filled = spot_res.get('filled_qty', spot_res.get('filled', 0))
                if filled > 0:
                    await self.client.place_order(symbol=spot_symbol, side='sell', order_type='market', quantity=filled)
            if perp_res:
                filled = perp_res.get('filled_qty', perp_res.get('filled', 0))
                if filled > 0:
                    await self.client.place_order(symbol=perp_symbol, side='buy', order_type='market', quantity=filled)
            await self.state_machine.transition(context, ExecutionState.UNWOUND)
        except Exception as e:
            logger.error(f"Failed to unwind funding rate entry: {e}")
            await self.state_machine.transition(context, ExecutionState.STUCK, data_updates={"unwind_error": str(e)})

    async def evaluate_exit(self, spot_symbol: str, perp_symbol: str, entry_timestamp: float) -> Dict[str, Any]:
        """
        Evaluates if we should exit an existing position.
        Spec Section 5.3 exit triggers:
        - Trailing avg funding deteriorated
        - Basis risk spike
        - Max holding period reached
        """
        hours_held = (time.time() - entry_timestamp) / 3600.0
        if hours_held < self.min_holding_hr:
            return {"exit": False, "reason": "Minimum holding period not reached"}
        
        # Check max holding period (Spec 5.3)
        days_held = hours_held / 24.0
        if days_held >= self.max_holding_days:
            return {"exit": True, "reason": f"Max holding period reached ({days_held:.1f} days)"}
            
        funding_analysis = await self._analyze_funding_history(perp_symbol)
        
        # Only exit on funding deterioration if we have data (prevent API blip exits)
        if not funding_analysis['viable'] and funding_analysis.get('reason') != "No funding history available":
            return {"exit": True, "reason": "Funding rate deteriorated: " + funding_analysis.get('reason', '')}
            
        spot_price = await self.client.get_mark_price(spot_symbol)
        perp_price = await self.client.get_mark_price(perp_symbol)
        
        if spot_price > 0:
            basis_pct = abs((perp_price - spot_price) / spot_price) * 100.0
            if basis_pct > self.max_basis_pct * 2:  # Exit threshold is 2x entry threshold
                return {"exit": True, "reason": f"Basis risk spike ({basis_pct:.2f}%)"}
            
        return {"exit": False, "reason": "Position healthy"}
