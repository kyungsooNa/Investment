"""백테스트 결과 조회 API."""
from fastapi import APIRouter, Query

from repositories.backtest_journal_repository import BacktestJournalRepository
from services.backtest_result_service import BacktestResultService
from view.web.api_common import _get_ctx


router = APIRouter()


@router.get("/backtest/results")
async def get_backtest_results(limit: int = Query(default=50, ge=1, le=200)):
    ctx = _get_ctx()
    repository = getattr(ctx, "backtest_journal_repository", None)
    if repository is None:
        repository = BacktestJournalRepository()
    results = BacktestResultService(repository).list_results(limit=limit)
    return {"results": results, "count": len(results)}
