"""Strategy identity resolver — P3-4 Phase 2.

Phase 1 (#446, 2026-05-23) 에서 각 LiveStrategy 에 `strategy_id` (영문 stable) 와
`name` (한국어 display) property 가 도입됐다. Phase 2 는 risk_gate / kill_switch /
virtual_trade journal / config 의 사용처를 strategy_name → strategy_id 기준으로
마이그레이션한다.

Resolver 는 두 표현 사이 양방향 변환과 미지값 passthrough 를 담당한다.
SQLite/JSON state 에 남아 있는 legacy 한국어 값을 in-memory 에서 strategy_id 로
정규화하여 consumer 가 strategy_id 만 신경 쓰면 되도록 한다.

매핑 출처: 각 strategy 파일의 `name` / `strategy_id` property (Phase 1 잠금).
신규 전략 추가 시에는 해당 strategy 파일과 이 dict 를 함께 갱신한다.

활성/실험/레거시 분류 (1차 대안 — metadata 기반 registry):
- ACTIVE: StrategyFactory.build() 가 자동 register 하는 운영 중 전략
- EXPERIMENTAL: 수동/백테스트 용도, 자동 register 미해당
- LEGACY: 사용 중단 예정 (현재 없음)
- UNKNOWN: Phase 1 매핑에 없는 strategy_id (passthrough)
"""
from __future__ import annotations

from enum import Enum


STRATEGY_DISPLAY_MAP: dict[str, str] = {
    "first_pullback": "첫눌림목",
    "high_tight_flag": "하이타이트플래그",
    "inverse_etf_regime": "인버스ETF레짐",
    "larry_williams_cb": "LarryWilliamsCB",
    "larry_williams_vbo": "래리윌리엄스VBO",
    "oneil_pocket_pivot": "오닐PP/BGU",
    "oneil_squeeze_breakout": "오닐스퀴즈돌파",
    "program_buy_follow": "프로그램매수추종",
    "rsi2_pullback": "RSI2눌림목",
    "traditional_volume_breakout": "거래량돌파(전통)",
    "volume_breakout_live": "거래량돌파",
}

_DISPLAY_TO_ID: dict[str, str] = {v: k for k, v in STRATEGY_DISPLAY_MAP.items()}


class StrategyStatus(str, Enum):
    """전략 운영 상태."""
    ACTIVE = "active"               # StrategyFactory 가 자동 register, 운영 중
    EXPERIMENTAL = "experimental"   # 수동/백테스트 용도, 자동 register 안 됨
    LEGACY = "legacy"               # 사용 중단 예정 (현재 없음)
    UNKNOWN = "unknown"             # Phase 1 매핑에 없는 strategy_id


# 출처: view/web/bootstrap/strategy_factory.py 의 build() 본문에서
#       ctx.scheduler.register(...) 가 호출되는 8개 = ACTIVE.
#       나머지 3개 (program_buy_follow / traditional_volume_breakout /
#       volume_breakout_live) 는 코드는 있으나 자동 register 안 됨 = EXPERIMENTAL.
STRATEGY_STATUS_MAP: dict[str, StrategyStatus] = {
    "first_pullback": StrategyStatus.ACTIVE,
    "high_tight_flag": StrategyStatus.ACTIVE,
    "inverse_etf_regime": StrategyStatus.ACTIVE,
    "larry_williams_cb": StrategyStatus.ACTIVE,
    "larry_williams_vbo": StrategyStatus.ACTIVE,
    "oneil_pocket_pivot": StrategyStatus.ACTIVE,
    "oneil_squeeze_breakout": StrategyStatus.ACTIVE,
    "rsi2_pullback": StrategyStatus.ACTIVE,
    "program_buy_follow": StrategyStatus.EXPERIMENTAL,
    "traditional_volume_breakout": StrategyStatus.EXPERIMENTAL,
    "volume_breakout_live": StrategyStatus.EXPERIMENTAL,
}


class StrategyIdentityResolver:
    """Bidirectional id ↔ display resolver with passthrough for unknown values.

    - `to_id(value)` — id 면 그대로, display 면 id 로 변환, 미지값이면 입력 그대로.
    - `to_display(value)` — display 면 그대로, id 면 display 로 변환, 미지값이면 입력 그대로.
    - `is_known_id(value)` — Phase 1 잠금 id 집합 membership 검사.

    빈 문자열 / None 입력은 빈 문자열로 안전 처리한다.
    """

    def to_id(self, value: str | None) -> str:
        if not value:
            return ""
        if value in STRATEGY_DISPLAY_MAP:
            return value
        return _DISPLAY_TO_ID.get(value, value)

    def to_display(self, value: str | None) -> str:
        if not value:
            return ""
        if value in _DISPLAY_TO_ID:
            return value
        return STRATEGY_DISPLAY_MAP.get(value, value)

    def is_known_id(self, value: str | None) -> bool:
        if not value:
            return False
        return value in STRATEGY_DISPLAY_MAP

    def get_status(self, value: str | None) -> StrategyStatus:
        """전략 운영 상태 반환. display 입력은 strategy_id 로 정규화 후 조회.

        Phase 1 매핑에 없는 strategy 는 UNKNOWN.
        """
        sid = self.to_id(value)
        return STRATEGY_STATUS_MAP.get(sid, StrategyStatus.UNKNOWN)


STRATEGY_IDENTITY_RESOLVER = StrategyIdentityResolver()
