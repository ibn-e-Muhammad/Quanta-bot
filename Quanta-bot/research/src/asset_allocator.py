class AdaptiveAssetAllocator:
    """
    Evaluates rolling historical metrics native to the executed timeframe array.
    Automatically prevents asset-tier freezing by computing real-time expectancy.
    """
    def __init__(self, window_size=25):
        self.window_size = window_size
        self.trade_history = [] 
        # tuple logic -> (outcome_int, pnl_pct_float)

    def add_trade(self, outcome, pnl_pct):
        self.trade_history.append((outcome, pnl_pct))
        if len(self.trade_history) > self.window_size:
            self.trade_history.pop(0)

    def get_current_tier_multiplier(self):
        if len(self.trade_history) < 10:
            return 1.0 # Default Neutral Initiation
            
        wins = sum(1 for o, _ in self.trade_history if o == 1)
        total = len(self.trade_history)
        wr = wins / total
        
        gp = sum(p for o, p in self.trade_history if o == 1)
        gl = abs(sum(p for o, p in self.trade_history if o == 0))
        pf = gp / gl if gl != 0 else float('inf')
        
        # Adaptive Stability Tiers recursively determined organically
        if pf > 1.20 and wr > 0.28:
            return 1.0 # Tier A (Top Quality Edge mapped safely)
        elif pf > 0.85:
            return 0.75 # Tier B (Neutral performance)
        else:
            return 0.50 # Tier C (Degrading logic - reduce exposure dynamically)
