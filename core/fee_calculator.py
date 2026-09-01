from typing import Dict, Any, List
from core.orderbook_manager import OrderBook

class FeeCalculator:
    """
    Comprehensive Net-Profit Model per Section 2.4.
    """
    def __init__(self, exchange_clients: Dict[str, Any]):
        self.exchange_clients = exchange_clients
        
    def walk_order_book(self, order_book: OrderBook, side: str, size: float) -> float:
        """
        Simulate walking the book for the intended order size to get a realistic average fill price.
        :param side: 'buy' or 'sell'
        :param size: The quantity of base asset to buy or sell
        :return: The average fill price. Raises ValueError if insufficient depth.
        """
        if side == 'buy':
            levels = order_book.asks
        elif side == 'sell':
            levels = order_book.bids
        else:
            raise ValueError("Side must be 'buy' or 'sell'")
            
        remaining_size = size
        total_cost = 0.0
        
        for price, qty in levels:
            if remaining_size <= 0:
                break
            
            fill_qty = min(remaining_size, qty)
            total_cost += fill_qty * price
            remaining_size -= fill_qty
            
        if remaining_size > 0:
            raise ValueError(f"Insufficient order book depth to {side} {size} {order_book.symbol}")
            
        return total_cost / size

    async def calculate_net_profit(
        self,
        strategy: str,
        legs: List[Dict[str, Any]],
        slippage_buffer_pct: float,
        latency_decay_estimate_pct: float,
        min_profit_threshold: float,
        cross_exchange_withdrawal_fee: float = 0.0,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Evaluates an opportunity.
        legs: list of dicts with {'exchange': str, 'symbol': str, 'side': 'buy'/'sell', 'size': float, 'order_book': OrderBook}
        """
        
        gross_pnl = 0.0
        total_fees = 0.0
        total_notional = 0.0
        details = []
        
        # 1. Walk the book for each leg
        for leg in legs:
            exchange = leg['exchange']
            symbol = leg['symbol']
            side = leg['side']
            size = leg['size']
            book = leg['order_book']
            
            rate_to_usd = leg.get('quote_to_usd_rate', 1.0)
            
            # Depth walk
            avg_price = self.walk_order_book(book, side, size)
            notional = size * avg_price
            notional_usd = notional * rate_to_usd
            
            # Fetch fee (mocked as taking from a client or cached config)
            fee_pct = 0.001 
            fee_cost_usd = notional_usd * fee_pct
            total_fees += fee_cost_usd
            
            if side == 'buy':
                gross_pnl -= notional_usd
            else:
                gross_pnl += notional_usd
                
            total_notional += notional_usd
                
            details.append({
                'exchange': exchange,
                'symbol': symbol,
                'side': side,
                'size': size,
                'avg_price': avg_price,
                'fee_cost_usd': fee_cost_usd
            })
            
        # Add cross exchange fees if Strategy B
        total_fees += cross_exchange_withdrawal_fee
        
        # Calculate gross spread before slippage/latency
        # Slippage and latency decay apply to the total notional traded, not just the profit.
        slippage_cost = total_notional * (slippage_buffer_pct / 100.0)
        latency_cost = total_notional * (latency_decay_estimate_pct / 100.0)
        
        # Special fix for triangular arbitrage: since legs buy/sell intermediate assets,
        # intermediate base assets cancel out. gross_pnl tracks the quote differences normalized to USD.
        # However, for a true round trip (USDT->BTC->ETH->USDT), the intermediate 'buys' are treated as spending USD
        # and the final 'sell' is receiving USD. 
        # But wait, Leg 2 buys ETH using BTC. It spends BTC (worth USD). 
        # If we subtract USD value of BTC spent, and Leg 1 bought BTC, Leg 1 spent USD and got BTC.
        # This double counts the spending.
        # Better: let the strategy pass explicit gross_pnl if it calculates it (like Triangular does).
        
        # Determine final gross pnl
        final_gross_pnl = kwargs.get('explicit_gross_pnl', gross_pnl)
        net_profit = final_gross_pnl - total_fees - slippage_cost - latency_cost
        
        is_viable = net_profit > min_profit_threshold
        
        return {
            "is_viable": is_viable,
            "net_profit": net_profit,
            "gross_pnl": final_gross_pnl,
            "total_fees": total_fees,
            "slippage_cost": slippage_cost,
            "latency_cost": latency_cost,
            "leg_details": details
        }
