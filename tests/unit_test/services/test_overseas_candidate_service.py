"""해외 후보 심볼 소스 테스트 (Phase 1-3).

OverseasStockCodeRepository(전 심볼) + 일봉 거래대금 필터로 watchlist 를 산출한다.
거래대금 하위 종목 제거 + 내림차순 정렬 + top_n 컷을 고정한다.
배선(VBO 주입)은 Phase 3 — 여기서는 독립 서비스만 검증.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from services.overseas_candidate_service import OverseasCandidateService
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


def _bars(close, volume, days=5):
    return [
        {"date": f"2026051{i}", "open": close, "high": close, "low": close,
         "close": close, "volume": volume}
        for i in range(1, days + 1)
    ]


@pytest.fixture
def svc():
    repo = MagicMock()
    # symbol -> (close, volume) → 일평균 거래대금 = close*volume
    repo.all_symbols.return_value = [
        {"s": "AAA", "n": "Aaa Inc", "e": "NASD"},   # 100 * 100000 = 10,000,000
        {"s": "BBB", "n": "Bbb Inc", "e": "NASD"},   # 50  * 1000   = 50,000   (하위)
        {"s": "CCC", "n": "Ccc Inc", "e": "NASD"},   # 200 * 500000 = 100,000,000 (최상위)
        {"s": "ZZZ", "n": "Zzz Inc", "e": "NYSE"},   # 다른 거래소 → 기본 제외
    ]

    tv = {
        "AAA": (100.0, 100000),
        "BBB": (50.0, 1000),
        "CCC": (200.0, 500000),
        "ZZZ": (300.0, 999999),
    }

    sqs = MagicMock()

    async def _ohlcv(symbol, limit=5, end_date=None, exchange=None):
        close, vol = tv[symbol]
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=_bars(close, vol))

    sqs.get_recent_daily_ohlcv = AsyncMock(side_effect=_ohlcv)

    service = OverseasCandidateService(
        overseas_stock_code_repository=repo,
        stock_query_service=sqs,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=service, repo=repo, sqs=sqs)


@pytest.mark.asyncio
async def test_filters_low_trading_value_and_sorts_desc(svc):
    result = await svc.service.get_candidates(
        exchange=OverseasExchange.NASD, min_avg_trading_value=1_000_000.0,
    )

    codes = [c["code"] for c in result]
    # BBB(5만) 제거, NYSE ZZZ 제외 → CCC(1억) > AAA(1천만) 내림차순
    assert codes == ["CCC", "AAA"]
    assert result[0]["avg_trading_value"] == 100_000_000.0
    assert result[0]["exchange"] == "NASD"
    assert result[0]["name"] == "Ccc Inc"


@pytest.mark.asyncio
async def test_top_n_caps_result(svc):
    result = await svc.service.get_candidates(
        exchange=OverseasExchange.NASD, min_avg_trading_value=0.0, top_n=1,
    )
    assert [c["code"] for c in result] == ["CCC"]


@pytest.mark.asyncio
async def test_explicit_symbols_override_repo(svc):
    result = await svc.service.get_candidates(
        exchange=OverseasExchange.NASD, symbols=["AAA"], min_avg_trading_value=0.0,
    )
    assert [c["code"] for c in result] == ["AAA"]
    svc.repo.all_symbols.assert_not_called()


@pytest.mark.asyncio
async def test_ohlcv_failure_excludes_symbol(svc):
    async def _ohlcv(symbol, limit=5, end_date=None, exchange=None):
        if symbol == "CCC":
            return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=[])
        close, vol = {"AAA": (100.0, 100000), "BBB": (50.0, 1000)}[symbol]
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=_bars(close, vol))

    svc.sqs.get_recent_daily_ohlcv = AsyncMock(side_effect=_ohlcv)

    result = await svc.service.get_candidates(
        exchange=OverseasExchange.NASD, min_avg_trading_value=1_000_000.0,
    )
    # CCC 실패 → 제외, AAA만 통과
    assert [c["code"] for c in result] == ["AAA"]


@pytest.mark.asyncio
async def test_passes_overseas_exchange_to_ohlcv(svc):
    await svc.service.get_candidates(exchange=OverseasExchange.NASD, symbols=["AAA"], min_avg_trading_value=0.0)
    # 일봉 조회가 해외 거래소 인자로 위임되는지 (Phase 1-1 어댑터 경유)
    _, kwargs = svc.sqs.get_recent_daily_ohlcv.await_args
    assert kwargs.get("exchange") == OverseasExchange.NASD
