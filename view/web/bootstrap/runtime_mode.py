"""RuntimeMode — `WebAppContext` 의 task/scheduler 등록 경계를 분기하는 enum.

- WEB     : 화면/API, NotificationQueueTask, WebSocket watchdog (TRADING 과 공유)
- TRADING : StrategyScheduler + 활성 전략, intraday tasks
- BATCH   : after-market task 그룹
- ALL     : 위 3 개 모두 (default. 현행 동작)

판정 패턴: `mode & RuntimeMode.X` 의 비트 truthiness 를 사용한다.
"""
from __future__ import annotations

import logging
import os
from enum import Flag, auto

_ENV_VAR_NAME = "RUNTIME_MODE"
_logger = logging.getLogger(__name__)


class RuntimeMode(Flag):
    WEB = auto()
    TRADING = auto()
    BATCH = auto()
    ALL = WEB | TRADING | BATCH

    @classmethod
    def from_env(cls, env: dict | None = None) -> "RuntimeMode":
        source = env if env is not None else os.environ
        raw = source.get(_ENV_VAR_NAME, "").strip()
        if not raw:
            return cls.ALL

        tokens = [t.strip().upper() for t in raw.replace(",", "|").split("|") if t.strip()]
        if not tokens:
            return cls.ALL

        result: RuntimeMode | None = None
        for token in tokens:
            member = cls.__members__.get(token)
            if member is None:
                _logger.warning(
                    "[RuntimeMode] unknown token %r in %s=%r; falling back to ALL.",
                    token, _ENV_VAR_NAME, raw,
                )
                return cls.ALL
            result = member if result is None else result | member
        return result if result is not None else cls.ALL
