def route_signal(regime):
    """
    Phase 5.4 — Expansion Only.
    All non-EXPANSION regimes produce no trade.
    Breakout and mean_reversion remain permanently culled.
    """
    if regime == "EXPANSION":
        return "expansion_engine"

    return "none_engine"
