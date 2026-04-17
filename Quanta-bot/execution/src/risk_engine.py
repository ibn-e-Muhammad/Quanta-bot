import json
import logging

class RiskEngine:
    """
    Room 3 Upgrade: Institutional Risk Controls.
    Handles ATR-based scaling, Daily Loss Locks, and Position Sizing.
    """
    def __init__(self, config_path):
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.logger = logging.getLogger("RiskEngine")
        
        # State tracking for the trading day
        self.daily_pnl = 0.0
        self.max_daily_loss = -0.05 # -5% of total account
        self.circuit_broken = False

    def check_circuit_breaker(self, current_balance):
        """Safety Gate: Stops all trading if daily drawdown is hit."""
        if self.daily_pnl <= (current_balance * self.max_daily_loss):
            self.circuit_broken = True
            self.logger.critical("[CRITICAL] Daily Loss Lock Triggered. All trading suspended.")
            return False
        return True

    def calculate_position_size(self, balance, risk_per_trade, entry_price, stop_loss_price, atr):
        """
        Elite Position Sizing:
        Combines fixed fractional risk with Volatility (ATR) scaling.
        """
        if self.circuit_broken:
            return 0.0

        # Risk Amount in Dollars (e.g., 2% of $1,000 = $20)
        risk_amount = balance * risk_per_trade
        
        # Price Distance to Stop Loss
        price_risk = abs(entry_price - stop_loss_price)
        
        if price_risk == 0:
            return 0.0

        # Raw Position Size based on SL distance
        raw_position_size = risk_amount / price_risk
        
        # ATR Scaling (Volatiltiy Filter)
        # If market is extremely volatile (High ATR), we reduce size further.
        volatility_multiplier = 1.0
        if atr > (entry_price * 0.02): # If ATR is > 2% of price
            volatility_multiplier = 0.5 # Cut size in half for safety
            
        final_position_size = raw_position_size * entry_price * volatility_multiplier
        
        # Final sanity check: Cap at 10x Max Leverage
        max_notional = balance * 10.0
        return min(final_position_size, max_notional)

    def update_pnl(self, pnl_change):
        """Updates the daily PnL tracker."""
        self.daily_pnl += pnl_change
        if self.daily_pnl < 0:
            self.logger.info(f"[RISK] Daily Drawdown: {self.daily_pnl}")
