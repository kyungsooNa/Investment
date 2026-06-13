VALID_MARKET_MODES = ("domestic", "overseas_us")


def market_mode_of(ctx) -> str:
    mode = getattr(ctx, "market_mode", "domestic")
    return mode if mode in VALID_MARKET_MODES else "domestic"


def enabled_market_modes_of(ctx) -> list[str]:
    active_mode = market_mode_of(ctx)
    raw_modes = getattr(ctx, "enabled_market_modes", None)
    if not isinstance(raw_modes, (list, tuple, set)):
        raw_modes = []

    modes: list[str] = []
    for mode in raw_modes:
        if mode in VALID_MARKET_MODES and mode not in modes:
            modes.append(mode)

    if active_mode not in modes:
        modes.append(active_mode)
    if not modes:
        modes.append("domestic")
    return modes


def is_market_enabled(ctx, market_mode: str) -> bool:
    return market_mode in enabled_market_modes_of(ctx)
