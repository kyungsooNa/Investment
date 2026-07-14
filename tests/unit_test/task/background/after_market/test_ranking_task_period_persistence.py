"""RankingTask 기간수급 DB 영속화 + 재시작 self-heal 테스트.

배경: TimeDispatcher는 거래일당 1회만 ranking_refresh 티켓을 발행하므로,
티켓 발행 이후 앱을 재시작하면 in-memory 기간수급 캐시가 비어도 당일
재예열이 없었다. DB 폴백과 시작 시 self-heal로 이를 복구한다.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from task.background.after_market.ranking_task import RankingTask


def _make_task(period_repo=None, mcs=None, worker_pool=None) -> RankingTask:
    broker = MagicMock()
    stock_repo = MagicMock()
    stock_repo.df = MagicMock()
    stock_repo.df.iterrows.return_value = iter([])
    return RankingTask(
        broker_api_wrapper=broker,
        stock_code_repository=stock_repo,
        logger=MagicMock(),
        market_calendar_service=mcs,
        worker_pool=worker_pool,
        period_ranking_repository=period_repo,
    )


def _make_mcs(market_open=False, latest_date="20260714") -> MagicMock:
    mcs = MagicMock()
    mcs.is_market_open_now = AsyncMock(return_value=market_open)
    mcs.get_latest_trading_date = AsyncMock(return_value=latest_date)
    return mcs


# ── _get_or_collect_period_ranking: DB 폴백/저장 ──────────────────


async def test_get_or_collect_loads_from_db_without_collecting():
    repo = MagicMock()
    repo.get.return_value = [{"a": "1"}]
    task = _make_task(period_repo=repo)
    task._collect_period_investor_program_ranking = AsyncMock()

    results = await task._get_or_collect_period_ranking(("20260714", 5))

    assert results == [{"a": "1"}]
    assert task._period_ranking_cache[("20260714", 5)] == [{"a": "1"}]
    task._collect_period_investor_program_ranking.assert_not_called()
    repo.get.assert_called_once_with("20260714", 5)


async def test_get_or_collect_saves_complete_results_to_db():
    repo = MagicMock()
    repo.get.return_value = None
    task = _make_task(period_repo=repo, mcs=_make_mcs(market_open=False))
    task._collect_period_investor_program_ranking = AsyncMock(
        return_value=([{"a": "1"}], True)
    )

    results = await task._get_or_collect_period_ranking(("20260714", 5))

    assert results == [{"a": "1"}]
    repo.save.assert_called_once_with("20260714", 5, [{"a": "1"}])


async def test_get_or_collect_skips_db_save_during_market_open():
    repo = MagicMock()
    repo.get.return_value = None
    task = _make_task(period_repo=repo, mcs=_make_mcs(market_open=True))
    task._collect_period_investor_program_ranking = AsyncMock(
        return_value=([{"a": "1"}], True)
    )

    await task._get_or_collect_period_ranking(("20260714", 5))

    repo.save.assert_not_called()
    # 기존 동작 유지: 메모리 캐시는 장중에도 채운다
    assert task._period_ranking_cache[("20260714", 5)] == [{"a": "1"}]


async def test_get_or_collect_does_not_save_incomplete_results():
    repo = MagicMock()
    repo.get.return_value = None
    task = _make_task(period_repo=repo, mcs=_make_mcs(market_open=False))
    task._collect_period_investor_program_ranking = AsyncMock(
        return_value=([{"a": "1"}], False)
    )

    await task._get_or_collect_period_ranking(("20260714", 5))

    repo.save.assert_not_called()
    assert ("20260714", 5) not in task._period_ranking_cache


async def test_get_or_collect_db_error_falls_back_to_collect():
    repo = MagicMock()
    repo.get.side_effect = RuntimeError("db broken")
    task = _make_task(period_repo=repo, mcs=_make_mcs(market_open=False))
    task._collect_period_investor_program_ranking = AsyncMock(
        return_value=([{"a": "1"}], True)
    )

    results = await task._get_or_collect_period_ranking(("20260714", 5))

    assert results == [{"a": "1"}]
    task._collect_period_investor_program_ranking.assert_called_once()


# ── get_period...: 비차단 온디맨드 수집 ────────────────────────────


async def test_get_period_returns_collecting_immediately_on_cache_miss():
    task = _make_task(mcs=_make_mcs(market_open=False, latest_date="20260714"))
    task._collect_period_investor_program_ranking = AsyncMock(
        return_value=(
            [{"hts_kor_isnm": "삼성전자", "combined_period_ntby_tr_pbmn_won": "100"}],
            True,
        )
    )

    resp = await task.get_period_investor_program_net_buy_ranking(days=5)

    assert resp.rt_cd == "0"
    assert resp.data == []
    assert "수집 중" in resp.msg1
    assert task._tasks, "백그라운드 수집 태스크가 스폰되어야 한다"

    await asyncio.gather(*task._tasks)

    resp2 = await task.get_period_investor_program_net_buy_ranking(days=5)
    assert resp2.data[0]["hts_kor_isnm"] == "삼성전자"
    task._collect_period_investor_program_ranking.assert_called_once()


async def test_get_period_returns_db_data_without_collecting():
    repo = MagicMock()
    repo.get.return_value = [
        {"hts_kor_isnm": "SK하이닉스", "combined_period_ntby_tr_pbmn_won": "920000000"}
    ]
    task = _make_task(period_repo=repo, mcs=_make_mcs(market_open=False, latest_date="20260714"))
    task._collect_period_investor_program_ranking = AsyncMock()

    resp = await task.get_period_investor_program_net_buy_ranking(days=5)

    assert resp.rt_cd == "0"
    assert resp.data[0]["hts_kor_isnm"] == "SK하이닉스"
    task._collect_period_investor_program_ranking.assert_not_called()
    assert not task._tasks


async def test_trigger_period_collection_dedups_in_progress():
    task = _make_task(mcs=_make_mcs())
    key = ("20260714", 5)
    task._period_ranking_tasks[key] = MagicMock()  # 수집 진행 중 시뮬레이션

    task._trigger_period_ranking_collection(key)

    assert not task._tasks, "진행 중이면 새 수집 태스크를 만들지 않는다"


# ── _period_ranking_self_heal: 재시작 복구 ─────────────────────────


async def test_self_heal_prewarms_when_cache_missing():
    task = _make_task(mcs=_make_mcs(market_open=False, latest_date="20260714"))
    task.prewarm_period_ranking = AsyncMock()

    await task._period_ranking_self_heal()

    task.prewarm_period_ranking.assert_awaited_once_with("20260714")


async def test_self_heal_skips_when_market_open():
    task = _make_task(mcs=_make_mcs(market_open=True))
    task.prewarm_period_ranking = AsyncMock()

    await task._period_ranking_self_heal()

    task.prewarm_period_ranking.assert_not_called()


async def test_self_heal_skips_when_memory_cache_exists():
    task = _make_task(mcs=_make_mcs(market_open=False, latest_date="20260714"))
    task._period_ranking_cache[("20260714", task.DEFAULT_PERIOD_RANKING_DAYS)] = []
    task.prewarm_period_ranking = AsyncMock()

    await task._period_ranking_self_heal()

    task.prewarm_period_ranking.assert_not_called()


async def test_self_heal_noop_without_mcs():
    task = _make_task(mcs=None)
    task.prewarm_period_ranking = AsyncMock()

    await task._period_ranking_self_heal()

    task.prewarm_period_ranking.assert_not_called()


async def test_start_spawns_self_heal_task():
    worker_pool = MagicMock()
    task = _make_task(mcs=_make_mcs(market_open=False), worker_pool=worker_pool)
    task._period_ranking_self_heal = AsyncMock()
    try:
        await task.start()
        assert task._tasks, "start()가 self-heal 태스크를 스폰해야 한다"
        # conftest가 asyncio.sleep을 패치하므로 스폰된 태스크를 직접 await 한다
        await asyncio.gather(*task._tasks)
        task._period_ranking_self_heal.assert_awaited_once()
    finally:
        await task.stop()
