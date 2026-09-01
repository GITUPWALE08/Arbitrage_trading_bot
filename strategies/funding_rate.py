import time
from typing import Dict, Any, List, Optional
from core.exchange_client import ExchangeClient
from core.fee_calculator import FeeCalculator
from core.logger import logging

logger = logging.getLogger("FundingRateStrategy")
logger.setLevel(logging.INFO)

class FundingRateStrategy:
    def __init__(
        self,
        exchange_client: ExchangeClient,
        fee_calculator: FeeCalculator,
        config: Dict[str, Any],
        state_store=None
    ):
        self.client = exchange_client
        self.fee_calc = fee_calculator
        self.config = config
        self.state_store = state_store
        
        # Parse config
        self.window_days = config.get('funding_history_window_days', 10)
        self.min_annualized_pct = config.get('min_trailing_annualized_funding_pct', 10.0)
        self.max_negative_flips = config.get('max_negative_flips_in_window', 2)
        self.max_basis_pct = config.get('max_basis_pct', 0.5)
        self.min_holding_hr = config.get('min_holding_period_hr', 24)
        
    def _annualize_rate(self, rate: float, interval_hours: int = 8) -> float:
        """Convert a single funding period rate to an annualized percentage."""
        periods_per_year = (24 / interval_hours) * 365
        return rate * periods_per_year * 100.0

    async def _analyze_funding_history(self, symbol: str) -> Dict[str, Any]:
        """
        Pulls historical window and calculates trailing average and stability.
        """
        history = await self.client.get_historical_funding_rates(symbol, self.window_days)
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
            
            # Log to DB per Section 10
            if self.state_store:
                await self.state_store.save_funding_rate(
                    exchange=self.client.exchange_name,
                    symbol=symbol,
                    rate=rate,
                    annualized_pct=ann_pct
                )
            
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

    async def evaluate_entry(self, spot_symbol: str, perp_symbol: str) -> Dict[str, Any]:
        """
        Evaluates if we should enter a cash-and-carry position.
        1. Check funding history stability/average.
        2. Check basis risk (spot vs futures gap).
        3. Note: Amortized cost check via FeeCalculator will be orchestrated at the execution engine level 
           before taking the trade, using the output of this evaluation.
        """
        funding_analysis = await self._analyze_funding_history(perp_symbol)
        if not funding_analysis['viable']:
            return {"enter": False, "reason": funding_analysis['reason']}
            
        # Check basis
        spot_price = await self.client.get_mark_price(spot_symbol)
        perp_price = await self.client.get_mark_price(perp_symbol)
        
        if spot_price <= 0:
            return {"enter": False, "reason": "Invalid spot price"}
            
        basis_pct = abs((perp_price - spot_price) / spot_price) * 100.0
        
        if basis_pct > self.max_basis_pct:
            return {"enter": False, "reason": f"Basis ({basis_pct:.2f}%) exceeds max allowed ({self.max_basis_pct}%)"}
            
        return {
            "enter": True,
            "avg_annualized_pct": funding_analysis['avg_annualized_pct'],
            "basis_pct": basis_pct,
            "spot_price": spot_price,
            "perp_price": perp_price
        }

    async def evaluate_exit(self, spot_symbol: str, perp_symbol: str, entry_timestamp: float) -> Dict[str, Any]:
        """
        Evaluates if we should exit an existing position.
        """
        hours_held = (time.time() - entry_timestamp) / 3600.0
        if hours_held < self.min_holding_hr:
            return {"exit": False, "reason": "Minimum holding period not reached"}
            
        funding_analysis = await self._analyze_funding_history(perp_symbol)
        
        # If funding no longer viable, we should exit
        if not funding_analysis['viable']:
            return {"exit": True, "reason": "Funding rate deteriorated: " + funding_analysis.get('reason', '')}
            
        spot_price = await self.client.get_mark_price(spot_symbol)
        perp_price = await self.client.get_mark_price(perp_symbol)
        basis_pct = abs((perp_price - spot_price) / spot_price) * 100.0
        
        if basis_pct > self.max_basis_pct:
            return {"exit": True, "reason": f"Basis risk spike ({basis_pct:.2f}%)"}
            
        return {"exit": False, "reason": "Position healthy"}
