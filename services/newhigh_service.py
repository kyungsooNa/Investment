from __future__ import annotations
import logging
import asyncio
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
        
        # 1차: DB 조회
        if self._stock_repository:
            try:
                latest_date = await self._stock_repository.get_latest_trade_date()
                if latest_date:
                    db_items = await self._stock_repository.get_newhigh_stocks(latest_date)
                    if db_items:
                        # 데이터 포매팅
                        data = [
                            {
                                "code": it.get("code", ""),
                                "name": it.get("name", ""),
                                "stck_prpr": str(it.get("current_price") or 0),
                                "prdy_ctrt": str(it.get("change_rate") or 0),
                                "rs_rating": it.get("rs_rating") or 0,
                                "market_cap": it.get("market_cap") or 0,
                                "trading_value": it.get("trading_value") or 0,
                                "w52_high": it.get("w52_high") or 0,
                                "is_historical_new_high": bool(it.get("is_historical_newhigh")),
                                "minervini_stage": it.get("minervini_stage") or 0,
                            }
                            for it in db_items
                        ]
                        return ResCommonResponse(rt_cd="0", msg1="성공", data=data)
            except Exception as e:
                self._logger.warning(f"NewHighService DB 조회 오류: {e}")

        # 2차: In-Memory 캐시
        task = self._newhigh_task
        if not task:
            return ResCommonResponse(rt_cd="1", msg1="NewHighTask 미설정", data=None)

        cache = await task.get_newhigh_cache()
        if cache:
            data = [
                {
                    "code": it.get("code", ""),
                    "name": it.get("name", ""),
                    "stck_prpr": str(it.get("current_price") or 0),
                    "prdy_ctrt": str(it.get("change_rate") or 0),
                    "rs_rating": it.get("rs_rating") or 0,
                    "market_cap": it.get("market_cap") or 0,
                    "trading_value": it.get("trading_value") or 0,
                    "w52_high": it.get("w52_high") or 0,
                    "is_historical_new_high": it.get("is_historical_new_high", False),
                    "minervini_stage": it.get("minervini_stage") or 0,
                }
                for it in cache
            ]
            return ResCommonResponse(rt_cd="0", msg1="성공", data=data)

        # 3차: 갱신 트리거 후 수집 대기
        progress = task.get_progress()
        if not progress.get("running"):
            asyncio.create_task(task.force_collect())
            
        return ResCommonResponse(rt_cd="0", msg1="수집 중", data=[])