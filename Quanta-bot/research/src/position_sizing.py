def calculate_dynamic_position_size(adx, volatility_regime, portfolio_factor, asset_tier_factor, vol_factor_high=0.5):
    """
    Computes final risk block dynamically based on active momentum, volatility, and correlation outputs.
    Yields Base Risk = 1.0R multiplied by scaling matrices statically.

    Args:
        vol_factor_high: Volatility sizing penalty for HIGH regime. Default 0.5 (Phase 7.4 baseline).
    """
    base_r = 1.0
    
    # 1. ADX Factor Mapping
    if adx < 20: adx_factor = 0.75
    elif adx <= 30: adx_factor = 1.0
    else: adx_factor = 1.25
        
    # 2. Volatility Modification (Phase 7.4 baseline)
    if volatility_regime == 'HIGH':
        vol_factor = vol_factor_high
    else:
        vol_factor = 1.0
    
    final_size = base_r * adx_factor * vol_factor * portfolio_factor * asset_tier_factor
    
    return final_size, adx_factor, vol_factor
