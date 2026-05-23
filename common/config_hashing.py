"""전략/시스템 config 의 deterministic hash 헬퍼 — P3-4 설정 변경 통제.

같은 config 는 같은 hash, 다른 config 는 다른 hash. 운영 중 config 변경을
사후에 추적할 수 있도록 TradeSignal / journal record 등에 stamp 한다.

지원 입력:
- pydantic BaseModel (model_dump 또는 dict() 로 정규화)
- dataclass (asdict)
- dict / list / 기본형
- None / empty → 빈 string

설계 원칙:
- 변환 불가 객체는 예외 대신 빈 string 반환 (운영 안전)
- 짧은 12자 hex digest (sha256 truncated) — log 가독성 우선
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any


def _normalize(value: Any) -> Any:
    """config 객체를 JSON-stable 형태로 정규화. 실패 시 None."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(v) for v in value]
    # pydantic v2 BaseModel
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _normalize(dump())
        except Exception:
            return None
    # pydantic v1 BaseModel
    dict_method = getattr(value, "dict", None)
    if callable(dict_method) and not isinstance(value, type):
        try:
            return _normalize(dict_method())
        except Exception:
            pass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return _normalize(dataclasses.asdict(value))
        except Exception:
            return None
    if hasattr(value, "__dict__") and not isinstance(value, type):
        try:
            return _normalize(vars(value))
        except Exception:
            return None
    return None


def compute_config_hash(config: Any) -> str:
    """config 의 deterministic 12자 hex hash 반환. 실패 / empty 시 빈 string."""
    if config is None:
        return ""
    normalized = _normalize(config)
    if normalized is None or (isinstance(normalized, (dict, list)) and not normalized):
        return ""
    try:
        payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return ""
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:12]
