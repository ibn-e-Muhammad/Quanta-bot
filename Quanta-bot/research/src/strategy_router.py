def route_signal(regime):
    """
    Maps structural regimes definitively to corresponding execution engines objectively.
    """
    if regime == "TRENDING":
        return "breakout_engine"
    elif regime == "CHOPPY":
        return "mean_reversion_engine"
    elif regime == "EXPANSION":
        return "expansion_engine"
    
    return "none_engine"
