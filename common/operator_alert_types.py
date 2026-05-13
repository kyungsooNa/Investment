"""운영자 알림 공통 타입 정의.

Kill Switch / Risk Gate 차단 이벤트를 NEW / ESCALATED / RESOLVED 세 가지
전이로 구분하여 중복 알림을 줄이기 위한 데이터 모델.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AlertTransition(str, Enum):
    """알림 상태 전이 유형."""
    NEW = "NEW"           # dedup_key 최초 발생
    ESCALATED = "ESCALATED"  # 동일 key severity 상승
    RESOLVED = "RESOLVED"    # 차단 해제 / 복구


class AlertSource(str, Enum):
    """알림 발생 소스."""
    KILL_SWITCH = "KILL_SWITCH"
    RISK_GATE = "RISK_GATE"
    RECONCILE = "RECONCILE"
    WEBSOCKET_WATCHDOG = "WEBSOCKET_WATCHDOG"
    DATA_QUALITY = "DATA_QUALITY"


# severity 순서 (낮을수록 심각)
_SEVERITY_ORDER = {"block": 0, "warning": 1, "error": 2, "critical": 3}


def severity_is_higher(new: str, old: str) -> bool:
    """new severity 가 old 보다 높으면 True."""
    return _SEVERITY_ORDER.get(new, 0) > _SEVERITY_ORDER.get(old, 0)


@dataclass
class OperatorAlert:
    """단일 운영자 알림 이벤트 (dedup 결과 포함)."""
    source: AlertSource
    dedup_key: str
    severity: str
    title: str
    message: str
    transition: AlertTransition
    timestamp: str              # ISO 8601 KST
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "dedup_key": self.dedup_key,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "transition": self.transition.value,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
