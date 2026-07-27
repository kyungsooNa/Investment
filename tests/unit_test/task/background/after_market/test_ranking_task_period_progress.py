"""RankingTask 기간수급 수집 진행률 테스트.

배경: 기간수급은 전체 종목을 순회하므로 온디맨드 수집이 10분 이상 걸린다.
웹 화면이 "수집 중" 스피너만 보여주면 진행 여부를 알 수 없으므로
투자자 랭킹과 분리된 별도 진행률 상태를 노출한다.
"""
from unittest.mock import AsyncMock, MagicMock

from common.types import ResCommonResponse
from task.background.after_market.ranking_task import RankingTask


def _make_task() -> RankingTask:
    broker = MagicMock()
    stock_repo = MagicMock()
    stock_repo.df = MagicMock()
    stock_repo.df.iterrows.return_value = iter([])
    return RankingTask(
        broker_api_wrapper=broker,
        stock_code_repository=stock_repo,
        logger=MagicMock(),
    )


def _ok_response(code: str) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data=[{
            "stck_bsop_date": "20260727",
            "stck_prpr": "70000",
            "frgn_ntby_qty": "100",
            "orgn_ntby_qty": "50",
            "frgn_ntby_tr_pbmn": "10",
            "orgn_ntby_tr_pbmn": "5",
            "whol_smtn_ntby_qty": "30",
            "whol_smtn_ntby_tr_pbmn": "3000000",
        }],
    )


def _stub_collection(task: RankingTask, count: int = 20) -> None:
    stocks = [(f"0059{i:02d}", f"종목{i}", "KOSPI") for i in range(count)]
    task._load_all_stocks = MagicMock(return_value=stocks)
    task._load_industry_map = AsyncMock(return_value={})
    task._fetch_with_retry = AsyncMock(
        side_effect=lambda api_call, code, *a, **kw: _ok_response(code)
    )


async def test_period_progress_starts_at_zero():
    task = _make_task()

    progress = task.get_period_ranking_progress()

    assert progress == {
        "running": False,
        "processed": 0,
        "total": 0,
        "collected": 0,
        "elapsed": 0.0,
    }


async def test_period_progress_reaches_total_after_collection():
    task = _make_task()
    _stub_collection(task, count=20)

    buckets, _ = await task._collect_period_investor_program_ranking("20260727")

    progress = task.get_period_ranking_progress()
    assert progress["total"] == 20
    assert progress["processed"] == 20
    assert progress["collected"] == len(buckets[max(buckets)])
    assert progress["running"] is False


async def test_period_progress_reports_partial_progress_while_running():
    task = _make_task()
    _stub_collection(task, count=20)
    snapshots = []

    async def _capture(api_call, code, *args, **kwargs):
        snapshots.append(task.get_period_ranking_progress())
        return _ok_response(code)

    task._fetch_with_retry = AsyncMock(side_effect=_capture)

    await task._collect_period_investor_program_ranking("20260727")

    assert snapshots, "수집 중 진행률 스냅샷이 있어야 한다"
    assert all(s["running"] is True for s in snapshots)
    assert all(s["total"] == 20 for s in snapshots)
    # API_CHUNK_SIZE=8 → 마지막 청크 시점에는 이미 일부가 처리돼 있어야 한다
    assert snapshots[-1]["processed"] > 0


async def test_period_progress_clears_running_on_error():
    task = _make_task()
    _stub_collection(task, count=20)
    task._suspend_event = MagicMock()
    task._suspend_event.wait = AsyncMock(side_effect=RuntimeError("boom"))

    try:
        await task._collect_period_investor_program_ranking("20260727")
    except RuntimeError:
        pass

    assert task.get_period_ranking_progress()["running"] is False


async def test_period_progress_is_separate_from_investor_progress():
    task = _make_task()
    _stub_collection(task, count=20)

    await task._collect_period_investor_program_ranking("20260727")

    assert task.get_investor_ranking_progress()["total"] == 0
    assert task.get_period_ranking_progress()["total"] == 20
