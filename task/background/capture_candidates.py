"""캡처 대상 종목 resolve 공용 로직.

장마감 후 microstructure 캡처 태스크(after_market)와 장중 프로그램매매 구독
태스크(intraday)가 동일한 후보 집합(보유 종목 우선 + Pool B 워치리스트)을
바라보도록 단일 소스로 유지한다. (todo 1-5)
"""
from __future__ import annotations

from typing import Optional


async def resolve_capture_codes(
    *,
    virtual_trade_service=None,
    universe_service=None,
    max_codes: Optional[int] = None,
    logger=None,
    task_name: str = "capture_candidates",
) -> list[str]:
    """캡처 대상 종목: 보유 종목 우선 + Pool B 워치리스트, 중복 제거 후 cap."""
    codes: list[str] = []
    if virtual_trade_service is not None:
        try:
            holds = virtual_trade_service.get_holds() or []
            codes.extend(str(h.get("code")) for h in holds if h.get("code"))
        except Exception as exc:
            if logger:
                logger.warning(f"{task_name}: 보유 종목 조회 실패 — {exc}")
    if universe_service is not None:
        try:
            watchlist = await universe_service.get_watchlist() or {}
            codes.extend(str(code) for code in watchlist.keys())
        except Exception as exc:
            if logger:
                logger.warning(f"{task_name}: 워치리스트 조회 실패 — {exc}")
    deduped = list(dict.fromkeys(code for code in codes if code))
    if max_codes is not None:
        return deduped[:max_codes]
    return deduped
