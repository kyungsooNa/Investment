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
"""
from __future__ import annotations


STRATEGY_DISPLAY_MAP: dict[str, str] = {
    "first_pullback": "첫눌림목",
    "high_tight_flag": "하이타이트플래그",
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


STRATEGY_IDENTITY_RESOLVER = StrategyIdentityResolver()
