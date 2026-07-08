"""resolve_capture_codes 단위 테스트 — 거래대금 랭킹 보충 포함 (todo 1-5 캡처 코퍼스 폭)."""
from unittest.mock import AsyncMock, MagicMock

from common.types import ErrorCode, ResCommonResponse
from task.background.capture_candidates import resolve_capture_codes


def _make_virtual_trade_service(codes):
    svc = MagicMock()
    svc.get_holds.return_value = [{"code": c} for c in codes]
    return svc


def _make_universe_service(codes):
    svc = MagicMock()
    svc.get_watchlist = AsyncMock(return_value={c: object() for c in codes})
    return svc


def _make_stock_query_service(items):
    svc = MagicMock()
    svc.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=items)
    )
    return svc


def _ranking_item(code, name=""):
    return {"mksc_shrn_iscd": code, "hts_kor_isnm": name}


async def test_ranking_codes_appended_after_holds_and_watchlist_with_dedup():
    sqs = _make_stock_query_service([
        _ranking_item("000001", "보유중복"),  # 보유와 중복 — 제거돼야 함
        _ranking_item("100001", "랭킹A"),
        _ranking_item("100002", "랭킹B"),
    ])
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service(["000001"]),
        universe_service=_make_universe_service(["000002"]),
        stock_query_service=sqs,
    )
    assert codes == ["000001", "000002", "100001", "100002"]


async def test_ranking_etf_prefix_excluded():
    sqs = _make_stock_query_service([
        _ranking_item("152100", "KODEX 200"),
        _ranking_item("100001", "랭킹A"),
        _ranking_item("305720", "kodex 2차전지산업"),  # 대소문자 무관 제외
    ])
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service([]),
        universe_service=_make_universe_service([]),
        stock_query_service=sqs,
    )
    assert codes == ["100001"]


async def test_ranking_fetch_exception_keeps_base_codes():
    sqs = MagicMock()
    sqs.get_top_trading_value_stocks = AsyncMock(side_effect=RuntimeError("boom"))
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service(["000001"]),
        universe_service=_make_universe_service(["000002"]),
        stock_query_service=sqs,
    )
    assert codes == ["000001", "000002"]


async def test_ranking_error_rt_cd_keeps_base_codes():
    sqs = MagicMock()
    sqs.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="err", data=None)
    )
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service(["000001"]),
        universe_service=_make_universe_service([]),
        stock_query_service=sqs,
    )
    assert codes == ["000001"]


async def test_ranking_limit_caps_ranking_additions():
    sqs = _make_stock_query_service([_ranking_item(f"10000{i}", f"랭킹{i}") for i in range(5)])
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service([]),
        universe_service=_make_universe_service([]),
        stock_query_service=sqs,
        ranking_limit=2,
    )
    assert codes == ["100000", "100001"]


async def test_max_codes_truncation_prioritizes_holds_and_watchlist():
    sqs = _make_stock_query_service([_ranking_item("100001", "랭킹A"), _ranking_item("100002", "랭킹B")])
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service(["000001"]),
        universe_service=_make_universe_service(["000002"]),
        stock_query_service=sqs,
        max_codes=3,
    )
    assert codes == ["000001", "000002", "100001"]


async def test_without_stock_query_service_behavior_unchanged():
    codes = await resolve_capture_codes(
        virtual_trade_service=_make_virtual_trade_service(["000001"]),
        universe_service=_make_universe_service(["000002"]),
    )
    assert codes == ["000001", "000002"]
