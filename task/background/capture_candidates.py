"""캡처 대상 종목 resolve 공용 로직.

장마감 후 microstructure 캡처 태스크(after_market)와 장중 프로그램매매 구독
태스크(intraday)가 동일한 후보 집합(보유 종목 우선 + Pool B 워치리스트 +
거래대금 랭킹 보충)을 바라보도록 단일 소스로 유지한다. (todo 1-5)

거래대금 랭킹 보충은 하락장 워치리스트 기근 시 캡처 코퍼스 폭을 확보하기 위한
캡처 전용 확장이다 — 트레이딩 후보/주문 경로와는 무관하다. (todo 1-5/1-8)
"""
from __future__ import annotations

from typing import Optional

from common.types import ErrorCode
from services.oneil_universe_service import _ETF_NAME_PREFIXES


async def _fetch_ranking_codes(
    stock_query_service,
    ranking_limit: int,
    logger,
    task_name: str,
) -> list[str]:
    """거래대금 상위 랭킹에서 캡처 보충 후보를 추출한다 (ETF 제외).

    랭킹 API는 실전 전용이므로 실패는 정상 경로로 간주하고 빈 목록을 반환한다.
    """
    try:
        resp = await stock_query_service.get_top_trading_value_stocks()
    except Exception as exc:
        if logger:
            logger.warning(f"{task_name}: 거래대금 랭킹 조회 실패 — {exc}")
        return []
    if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
        return []
    codes: list[str] = []
    for stock in (resp.data or []):
        if isinstance(stock, dict):
            code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
            name = stock.get("hts_kor_isnm", "")
        else:
            code = getattr(stock, "mksc_shrn_iscd", "") or getattr(stock, "stck_shrn_iscd", "")
            name = getattr(stock, "hts_kor_isnm", "")
        if not code:
            continue
        normalized = str(name or "").strip().upper()
        if any(normalized.startswith(prefix) for prefix in _ETF_NAME_PREFIXES):
            continue
        codes.append(str(code))
        if len(codes) >= ranking_limit:
            break
    return codes


async def resolve_capture_codes(
    *,
    virtual_trade_service=None,
    universe_service=None,
    stock_query_service=None,
    ranking_limit: int = 20,
    max_codes: Optional[int] = None,
    logger=None,
    task_name: str = "capture_candidates",
) -> list[str]:
    """캡처 대상 종목: 보유 우선 + Pool B 워치리스트 + 거래대금 랭킹 보충, 중복 제거 후 cap."""
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
    if stock_query_service is not None:
        base_count = len(dict.fromkeys(code for code in codes if code))
        ranking_codes = await _fetch_ranking_codes(
            stock_query_service, ranking_limit, logger, task_name
        )
        codes.extend(ranking_codes)
        if ranking_codes and logger:
            logger.info(
                f"{task_name}: 캡처 후보 랭킹 보충 — 기본 {base_count}종목 + 랭킹 {len(ranking_codes)}종목"
            )
    deduped = list(dict.fromkeys(code for code in codes if code))
    if max_codes is not None:
        return deduped[:max_codes]
    return deduped
