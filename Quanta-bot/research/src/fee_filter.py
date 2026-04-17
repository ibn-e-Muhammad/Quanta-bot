def is_fee_viable(entry_price, sl_price, notional):
    """
    Phase 4.65: Feasibility Sentry Engine
    Determines objectively if mathematical slippage outweighs local rewards natively rendering trades lethal.
    """
    price_risk = abs(entry_price - sl_price)
    if entry_price == 0:
        return False
        
    # 1. Theoretical 1R Profit Gross Calculation natively mapped
    theoretical_1r_profit = notional * (price_risk / entry_price)
    
    # 2. Complete Double-Friction Calculus 
    estimated_fees = notional * 0.0005 * 2
    estimated_slippage = notional * 0.0002
    total_friction = estimated_fees + estimated_slippage
    
    # 3. Mandatory 1.5x Reward Margin Limits structurally
    if theoretical_1r_profit < (total_friction * 1.5):
        return False
        
    return True
