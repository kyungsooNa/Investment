"""장중 캡처에 기록된 날짜별 후보군을 백테스트 universe로 재생한다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from common.types import ErrorCode, ResCommonResponse


def _safe_number(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


class CapturedCandidateProvider:
    def __init__(self, candidates_by_date: dict[str, dict[str, set[str]]]) -> None:
        self._candidates_by_date = candidates_by_date

    @classmethod
    def from_replay_dir(cls, replay_dir: str | Path) -> "CapturedCandidateProvider":
        by_date: dict[str, dict[str, set[str]]] = {}
        for path in sorted(Path(replay_dir).glob("replay_microstructure_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            metadata = payload.get("metadata") or {}
            trade_date = str(metadata.get("trade_date") or "").replace("-", "")
            sources = metadata.get("candidate_sources") or {}
            if len(trade_date) != 8 or not isinstance(sources, dict):
                continue
            by_date[trade_date] = {
                str(source): {
                    str(code).strip() for code in (codes or []) if str(code).strip()
                }
                for source, codes in sources.items()
                if isinstance(codes, list)
            }
        return cls(by_date)

    def codes_as_of(self, date_ymd: str, *, source: str = "base") -> set[str]:
        key = str(date_ymd or "").replace("-", "")
        return set((self._candidates_by_date.get(key) or {}).get(source) or set())


class CapturedCandidateUniverse:
    """현재 universe를 날짜별 캡처 base 후보로 제한하고 누락 후보를 합성한다."""

    def __init__(
        self,
        base: Any,
        *,
        provider: CapturedCandidateProvider,
        sqs: Any,
        clock: Any,
        item_factory: Callable[..., Any],
        source: str = "base",
        trading_value_lookback: int = 5,
    ) -> None:
        self._base = base
        self._provider = provider
        self._sqs = sqs
        self._clock = clock
        self._item_factory = item_factory
        self._source = source
        self._lookback = int(trading_value_lookback)

    async def get_watchlist(self, logger=None) -> dict:
        base_watchlist = (
            await self._base.get_watchlist(logger=logger)
            if logger is not None
            else await self._base.get_watchlist()
        )
        base_watchlist = dict(base_watchlist or {})
        trade_date = self._clock.get_current_kst_time().strftime("%Y%m%d")
        captured = self._provider.codes_as_of(trade_date, source=self._source)
        result = {code: item for code, item in base_watchlist.items() if code in captured}
        for code in sorted(captured - set(result)):
            avg_trading_value = await self._avg_trading_value(code, trade_date)
            result[code] = self._item_factory(
                code=code,
                name=code,
                market="",
                avg_trading_value_5d=avg_trading_value,
            )
        return result

    async def _avg_trading_value(self, code: str, trade_date: str) -> float:
        response = await self._sqs.get_recent_daily_ohlcv(
            code,
            limit=self._lookback,
            end_date=trade_date,
        )
        if isinstance(response, ResCommonResponse):
            if response.rt_cd != ErrorCode.SUCCESS.value:
                return 0.0
            rows = response.data or []
        else:
            rows = response or []
        values = [
            _safe_number(row.get("close")) * _safe_number(row.get("volume"))
            for row in rows
            if isinstance(row, dict)
        ]
        valid = [value for value in values if value > 0]
        return sum(valid) / len(valid) if valid else 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)
