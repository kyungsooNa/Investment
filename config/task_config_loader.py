# config/task_config_loader.py
"""task_config.yaml에서 스케줄러 설정을 로드하는 유틸리티."""
from __future__ import annotations

import os
from typing import Dict

import yaml
from pydantic import BaseModel, Field


class _AfterMarketTasksConfig(BaseModel):
    after_market_delay_sec: Dict[str, int] = Field(default_factory=dict)


class _TaskConfigModel(BaseModel):
    after_market_tasks: _AfterMarketTasksConfig = Field(default_factory=_AfterMarketTasksConfig)


_TASK_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "task_config.yaml",
)
_CACHED: Dict[str, int] = {}


def load_after_market_delays() -> Dict[str, int]:
    """task_config.yaml의 after_market_delay_sec를 {task_name: seconds} 로 반환한다.

    값은 분(minute) 단위로 저장되어 있으며 초(second)로 변환하여 반환한다.
    결과는 모듈 수준에서 캐시된다.
    """
    global _CACHED
    if _CACHED:
        return _CACHED
    try:
        with open(_TASK_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        config = _TaskConfigModel(**raw)
        _CACHED = {k: v * 60 for k, v in config.after_market_tasks.after_market_delay_sec.items()}
    except Exception:
        _CACHED = {}
    return _CACHED
