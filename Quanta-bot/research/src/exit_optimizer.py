def get_dynamic_exit_targets(entry_price, sl_price, signal):
    """
    Phase 4.65: Dynamic Partial Target Matrix
    Evaluates execution geometries statically feeding slicing pipelines inherently.
    """
    price_risk = abs(entry_price - sl_price)
    
    if signal == 1:
        tp1 = entry_price + price_risk
        tp2 = entry_price + (price_risk * 3)
    else:
        tp1 = entry_price - price_risk
        tp2 = entry_price - (price_risk * 3)
        
    return tp1, tp2
