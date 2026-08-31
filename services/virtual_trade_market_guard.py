"""Guards for the domestic virtual trade journal."""


def is_domestic_virtual_trade_code(code: str) -> bool:
    """Return True only for domestic stock codes handled by VirtualTradeRepository."""
    text = str(code or "").strip()
    return len(text) == 6 and text.isdigit()
