"""Minervini Stage2 point-in-time 조회 — 장마감 캡처·기간 백테스트 공용 (todo 1-8).

라이브 `MinerviniStageService.get_stage2_list()` 는 daily_prices 의 **최신** 거래일을
읽는다. 장중 D 일에 그 값은 D-1 장마감 기준 Stage2 목록이다. 과거 날짜를 재생할 때
같은 메서드를 쓰면 오늘의 목록이 과거에 섞이므로(#1050 VBO 랭킹 오염과 같은 유형),
여기서는 항상 "평가일 직전 저장 거래일" 의 목록·스냅샷만 읽는다.
"""
from __future__ import annotations

from typing import Any, Optional

from common.types import ErrorCode, ResCommonResponse

STAGE_UNKNOWN = 0
# Stage2 목록 밖이라는 사실만 알 수 있어 1/3/4 를 구분하지 않는다 — 청산 판정은 `!= 2` 만 본다.
NOT_STAGE2 = -1


def _to_live_item(row: dict) -> dict:
    """daily_prices 행을 라이브 `get_stage2_list()` 응답 항목 형태로 맞춘다."""
    return {
        "code": row.get("code", ""),
        "name": row.get("name", ""),
        "stck_prpr": str(row.get("current_price") or 0),
        "prdy_ctrt": str(row.get("change_rate") or 0),
        "stage": row.get("minervini_stage", 2),
        "rs_rating": row.get("rs_rating") or 0,
        "market_cap": row.get("market_cap") or 0,
    }


async def load_pit_stage2_items(
    stock_repository: Any,
    as_of_date: str,
) -> tuple[Optional[str], list[dict]]:
    """`as_of_date` 장중에 라이브 전략이 보던 Stage2 목록(직전 거래일 장마감 기준)."""
    previous_date = await stock_repository.get_latest_trade_date_before(as_of_date)
    if not previous_date:
        return None, []
    rows = await stock_repository.get_minervini_stage2_stocks(previous_date)
    return previous_date, [_to_live_item(row) for row in rows or []]


class BacktestMinerviniStageService:
    """기간 백테스트에서 `MinerviniStageService` 를 대신하는 시점 고정 어댑터."""

    def __init__(self, stock_repository: Any, *, classifier: Any) -> None:
        self._repo = stock_repository
        self._classifier = classifier
        self._backtest_date: Optional[str] = None
        self._stage2_cache: dict[str, tuple[Optional[str], set[str]]] = {}

    def set_backtest_date(self, date_ymd: str) -> None:
        self._backtest_date = str(date_ymd)

    async def get_stage2_list(self) -> ResCommonResponse:
        if not self._backtest_date:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="backtest date not set for Minervini Stage2 replay",
                data=None,
            )
        _, items = await load_pit_stage2_items(self._repo, self._backtest_date)
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=items)

    async def get_stage_for_code(self, code: str) -> tuple[int, str]:
        """보유 종목의 Stage2 유지 여부 — 직전 거래일 장마감 Stage2 목록 소속으로 판정.

        라이브 청산은 최신(=직전 거래일) DB 스냅샷의 Stage 를 쓴다. 청산 판정은
        `stage != 2` 만 보므로 Stage2 목록 소속 여부로 충분하다. 그 날 Stage 계산
        자체가 없으면 판정 불가(0)를 돌려준다 — 라이브의 데이터 부족(0)과 같이 청산
        사유가 되지만, 사유 문자열로 "목록 밖"과 "스냅샷 없음"을 구분해 남긴다.
        """
        if not self._backtest_date:
            return STAGE_UNKNOWN, "backtest date not set"
        previous_date, codes = await self._stage2_codes(self._backtest_date)
        if code in codes:
            return 2, f"(DB {previous_date} Stage2)"
        if previous_date and await self._repo.get_minervini_stage_count(previous_date) > 0:
            return NOT_STAGE2, f"(DB {previous_date} Stage2 목록 밖)"
        return STAGE_UNKNOWN, "직전 거래일 Stage 스냅샷 없음"

    async def _stage2_codes(self, as_of_date: str) -> tuple[Optional[str], set[str]]:
        if as_of_date not in self._stage2_cache:
            previous_date, items = await load_pit_stage2_items(self._repo, as_of_date)
            self._stage2_cache[as_of_date] = (
                previous_date,
                {str(item.get("code")) for item in items if item.get("code")},
            )
        return self._stage2_cache[as_of_date]

    def classify_stage_series(self, *args, **kwargs):
        return self._classifier.classify_stage_series(*args, **kwargs)
