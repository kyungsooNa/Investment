"""RankingTask 기간수급 전 구간 파생 테스트.

배경: 투자자/프로그램 일별 API는 요청에 기간이 없고 응답을 잘라 쓰는 구조라
(days는 output[:days] 슬라이스일 뿐) 1D 스윕과 20D 스윕의 API 호출 수가 같다.
따라서 전 종목 순회는 최장 구간 기준 1회만 하고 나머지 구간은 같은 행에서 파생한다.
"""
from unittest.mock import AsyncMock, MagicMock

from common.types import ResCommonResponse
from task.background.after_market.ranking_task import RankingTask

TRADING_DATES = [
    "20260707", "20260708", "20260709", "20260710", "20260713",
    "20260714", "20260715", "20260716", "20260717", "20260720",
    "20260721", "20260722", "20260723", "20260724", "20260727",
    "20260728", "20260729", "20260730", "20260731", "20260803",
]  # 오래된 순 20거래일 (마지막이 기준일)


def _make_task(period_repo=None, mcs=None) -> RankingTask:
    broker = MagicMock()
    stock_repo = MagicMock()
    stock_repo.df = MagicMock()
    stock_repo.df.iterrows.return_value = iter([])
    return RankingTask(
        broker_api_wrapper=broker,
        stock_code_repository=stock_repo,
        logger=MagicMock(),
        market_calendar_service=mcs,
        period_ranking_repository=period_repo,
    )


def _investor_rows() -> list[dict]:
    """최신일부터 과거 순. 하루당 외국인 1주, 기관 1주 순매수."""
    return [
        {
            "stck_bsop_date": date,
            "stck_prpr": "70000",
            "frgn_ntby_qty": "1",
            "orgn_ntby_qty": "1",
            "frgn_ntby_tr_pbmn": "1",
            "orgn_ntby_tr_pbmn": "1",
        }
        for date in reversed(TRADING_DATES)
    ]


def _program_rows() -> list[dict]:
    return [
        {
            "stck_bsop_date": date,
            "whol_smtn_ntby_qty": "1",
            "whol_smtn_ntby_tr_pbmn": "1000000",
        }
        for date in reversed(TRADING_DATES)
    ]


def _stub_sweep(task: RankingTask, count: int = 2) -> MagicMock:
    stocks = [(f"0059{i:02d}", f"종목{i}", "KOSPI") for i in range(count)]
    task._load_all_stocks = MagicMock(return_value=stocks)
    task._load_industry_map = AsyncMock(return_value={})
    task._get_recent_trading_dates = AsyncMock(
        side_effect=lambda date, days: TRADING_DATES[-days:]
    )

    async def _fetch(api_call, code, target_date, days):
        rows = _program_rows() if "program" in str(api_call) else _investor_rows()
        return ResCommonResponse(rt_cd="0", msg1="정상", data=rows[:days])

    fetch_mock = AsyncMock(side_effect=_fetch)
    task._fetch_with_retry = fetch_mock
    return fetch_mock


async def test_single_sweep_fills_every_bucket():
    task = _make_task()
    _stub_sweep(task, count=2)

    buckets, is_complete = await task._collect_period_investor_program_ranking("20260803")

    assert is_complete is True
    assert set(buckets) == set(RankingTask.PERIOD_RANKING_PREWARM_DAYS)
    for days in RankingTask.PERIOD_RANKING_PREWARM_DAYS:
        assert len(buckets[days]) == 2, f"{days}일 구간 결과 누락"


async def test_sweep_api_call_count_is_independent_of_bucket_count():
    """구간이 5개여도 종목당 호출은 투자자 1 + 프로그램 1 뿐이어야 한다."""
    task = _make_task()
    fetch_mock = _stub_sweep(task, count=3)

    await task._collect_period_investor_program_ranking("20260803")

    assert fetch_mock.await_count == 3 * 2


async def test_buckets_sum_only_their_own_trading_dates():
    """3일 구간은 최근 3거래일만, 20일 구간은 20거래일 전부를 합산한다."""
    task = _make_task()
    _stub_sweep(task, count=1)

    buckets, _ = await task._collect_period_investor_program_ranking("20260803")

    # 하루당 외국인 1주 → 구간 일수만큼 누적
    assert buckets[3][0]["frgn_period_ntby_qty"] == "3"
    assert buckets[5][0]["frgn_period_ntby_qty"] == "5"
    assert buckets[20][0]["frgn_period_ntby_qty"] == "20"
    assert buckets[3][0]["period_days"] == "3"
    assert buckets[3][0]["earliest_trading_date"] == TRADING_DATES[-3]
    assert buckets[20][0]["earliest_trading_date"] == TRADING_DATES[0]


async def test_get_or_collect_caches_and_saves_every_bucket():
    repo = MagicMock()
    repo.get.return_value = None
    mcs = MagicMock()
    mcs.is_market_open_now = AsyncMock(return_value=False)
    task = _make_task(period_repo=repo, mcs=mcs)
    task._collect_period_investor_program_ranking = AsyncMock(
        return_value=({3: [{"a": "3"}], 5: [{"a": "5"}]}, True)
    )

    results = await task._get_or_collect_period_ranking(("20260803", 3))

    assert results == [{"a": "3"}]
    assert task._period_ranking_cache[("20260803", 3)] == [{"a": "3"}]
    assert task._period_ranking_cache[("20260803", 5)] == [{"a": "5"}]
    assert repo.save.call_count == 2


async def test_second_bucket_request_reuses_in_flight_sweep():
    """다른 구간 요청이 같은 날짜의 스윕을 중복 시작하지 않는다."""
    task = _make_task(mcs=None)
    task._period_ranking_tasks["20260803"] = MagicMock()  # 스윕 진행 중 시뮬레이션

    task._trigger_period_ranking_collection(("20260803", 3))

    assert not task._tasks, "이미 진행 중이면 새 스윕을 만들지 않는다"
