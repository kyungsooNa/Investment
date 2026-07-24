"""공개 응답과 로그의 민감정보 마스킹."""
from __future__ import annotations

from core.loggers.sensitive_data_filter import redact_sensitive_text

_SECRET_PARTS = (
    "api_key",
    "api_secret",
    "secret_key",
    "access_token",
    "refresh_token",
    "password",
    "authorization",
)
_ACCOUNT_KEYS = {
    "account_number",
    "stock_account_number",
    "paper_stock_account_number",
    "cano",
    "acnt_no",
}
_ORDER_KEYS = {
    "ord_no",
    "order_no",
    "odno",
    "execution_no",
    "fill_no",
}
_MONEY_PARTS = (
    "amount",
    "cash",
    "asset",
    "equity",
    "evlu",
    "_amt",
    "pnl_won",
    "평가금액",
    "총자산",
    "현금",
)

def _mask_identifier(value) -> str:
    text = str(value)
    if len(text) <= 4:
        return text
    return "*" * (len(text) - 4) + text[-4:]


def _is_secret_key(key: str) -> bool:
    return any(part in key for part in _SECRET_PARTS)


def _is_money_key(key: str) -> bool:
    return any(part in key for part in _MONEY_PARTS)


def mask_sensitive_data(value, *, parent_key: str = ""):
    """중첩 응답을 복사하면서 공개 가능한 형태로 변환한다."""
    if isinstance(value, dict):
        result = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower()
            if _is_secret_key(normalized):
                result[raw_key] = "[REDACTED]"
            elif normalized in _ACCOUNT_KEYS or (
                parent_key == "account_info" and normalized == "number"
            ):
                result[raw_key] = _mask_identifier(item)
            elif normalized in _ORDER_KEYS:
                result[raw_key] = _mask_identifier(item)
            elif _is_money_key(normalized):
                result[raw_key] = None
            else:
                result[raw_key] = mask_sensitive_data(item, parent_key=normalized)
        return result
    if isinstance(value, list):
        return [mask_sensitive_data(item, parent_key=parent_key) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_sensitive_data(item, parent_key=parent_key) for item in value)
    return value
