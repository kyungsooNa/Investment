from __future__ import annotations
import logging
from typing import Optional, TYPE_CHECKING
from common.types import ResCommonResponse

if TYPE_CHECKING:
    from repositories.stock_repository import StockRepository
    from task.background.after_market.newhigh_task import NewHighTask


class NewHighService:
    """
    52주 신고가(역사적 신고가 포함) 종목을 제공하는 서비스.
    DB 및 NewHighTask 캐시 연동을 담당한다.
    """

    def __init__(
        self,
        stock_repository: Optional["StockRepository"] = None,
        newhigh_task: Optional["NewHighTask"] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._stock_repository = stock_repository
        self._newhigh_task = newhigh_task
        self._logger = logger or logging.getLogger(__name__)

    async def get_newhigh_list(self) -> ResCommonResponse:
        """신고가 종목 목록을 조회한다 (DB -> Task Cache -> Task Trigger)."""
        latest_date = None

        # 1차: DB 조회
        if self._stock_repository:
            try:
                latest_date = await self._stock_repository.get_latest_trade_date()
                if latest_date:
                    db_items = await self._stock_repository.get_newhigh_stocks(latest_date)
                    if db_items:
                        return ResCommonResponse(
                            rt_cd="0",
                            msg1="성공",
                            data=[self._format_db_item(it) for it in db_items],
                        )
            except Exception as e:
                self._logger.warning(f"NewHighService DB 조회 오류: {e}")

        task = self._newhigh_task
        if not task:
            return ResCommonResponse(rt_cd="1", msg1="NewHighTask 미설정", data=None)

        progress = task.get_progress()
        if (
            latest_date
            and progress.get("last_date") == latest_date
            and not progress.get("running")
            and int(progress.get("newhigh_count") or 0) == 0
        ):
            return ResCommonResponse(rt_cd="0", msg1="성공", data=[])

        # 2차: In-Memory 캐시
        cache = await task.get_newhigh_cache()
        if cache:
            return ResCommonResponse(
                rt_cd="0",
                msg1="성공",
                data=[self._format_cache_item(it) for it in cache],
            )

        # 3차: 갱신 트리거 후 수집 대기
        progress = task.get_progress()
        if not progress.get("running"):
            trigger_refresh = getattr(task, "trigger_refresh", None)
            if callable(trigger_refresh):
                trigger_refresh()

        return ResCommonResponse(rt_cd="0", msg1="수집 중", data=[])

    @staticmethod
    def _format_db_item(item: dict) -> dict:
        return {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "stck_prpr": str(item.get("current_price") or 0),
            "prdy_ctrt": str(item.get("change_rate") or 0),
            "rs_rating": item.get("rs_rating") or 0,
            "market_cap": item.get("market_cap") or 0,
            "trading_value": item.get("trading_value") or 0,
            "w52_high": item.get("w52_high") or 0,
            "is_historical_new_high": bool(item.get("is_historical_newhigh")),
            "minervini_stage": item.get("minervini_stage") or 0,
        }

    @staticmethod
    def _format_cache_item(item: dict) -> dict:
        return {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "stck_prpr": str(item.get("current_price") or 0),
            "prdy_ctrt": str(item.get("change_rate") or 0),
            "rs_rating": item.get("rs_rating") or 0,
            "market_cap": item.get("market_cap") or 0,
            "trading_value": item.get("trading_value") or 0,
            "w52_high": item.get("w52_high") or 0,
            "is_historical_new_high": item.get("is_historical_new_high", False),
            "minervini_stage": item.get("minervini_stage") or 0,
        }
