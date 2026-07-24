"""로그 레코드의 이름 있는 인증 비밀을 제거한다."""
import logging
import re

_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|password|authorization)\b(\s*[:=]\s*)([\"']?)([^,\s\"']+)([\"']?)"
)


def redact_sensitive_text(text) -> str:
    raw = str(text)

    def _replace(match: re.Match) -> str:
        quote = match.group(3)
        closing_quote = quote if quote and match.group(5) else ""
        return f"{match.group(1)}{match.group(2)}{quote}[REDACTED]{closing_quote}"

    return _NAMED_SECRET_RE.sub(_replace, raw)


def _redact_structure(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(
                part in normalized
                for part in (
                    "api_key",
                    "api_secret",
                    "secret_key",
                    "access_token",
                    "refresh_token",
                    "password",
                    "authorization",
                )
            ):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact_structure(item)
        return result
    if isinstance(value, list):
        return [_redact_structure(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_structure(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, (dict, list, tuple)):
            record.msg = _redact_structure(record.msg)
        else:
            record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        return True
