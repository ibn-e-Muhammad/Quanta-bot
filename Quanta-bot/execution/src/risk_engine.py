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
        self.default_leverage = 20.0
        self.margin_fraction = 0.10

    def check_circuit_breaker(self, current_balance):
        """Safety Gate: Stops all trading if daily drawdown is hit."""
        if self.daily_pnl <= (current_balance * self.max_daily_loss):
            self.circuit_broken = True
            self.logger.critical("[CRITICAL] Daily Loss Lock Triggered. All trading suspended.")
            return False
        return True

    def calculate_position_size(self, balance, risk_per_trade, entry_price, stop_loss_price, atr):
        """
        Small Account Compounding Model (Phase M1):
        - 20x leverage
        - Margin per trade = 10% of available balance
        - Quantity sized from notional = margin * leverage
        """
        if self.circuit_broken:
            return 0.0

        if entry_price <= 0:
            return 0.0

        margin = balance * self.margin_fraction
        notional = margin * self.default_leverage

        # Binance minimum notional guardrail (~$5-$10)
        if notional < 5.0:
            return 0.0

        quantity = notional / entry_price
        return max(0.0, quantity)

    def update_pnl(self, pnl_change):
        """Updates the daily PnL tracker."""
        self.daily_pnl += pnl_change
        if self.daily_pnl < 0:
            self.logger.info(f"[RISK] Daily Drawdown: {self.daily_pnl}")
