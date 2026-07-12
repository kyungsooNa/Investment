from __future__ import annotations
import logging
from typing import Optional, TYPE_CHECKING
from common.types import ResCommonResponse
from interfaces.refresh_task import NewHighRefreshTask

if TYPE_CHECKING:
    from repositories.stock_repository import StockRepository


class NewHighService:
    """
    52주 신고가(역사적 신고가 포함) 종목을 제공하는 서비스.
    DB 및 NewHighTask 캐시 연동을 담당한다.
    """

    # DB/캐시 양쪽 경로의 최대 반환 개수 (NewHighTask.get_newhigh_cache 기본값과 일치)
    _MAX_ITEMS = 200

    def __init__(
        self,
        stock_repository: Optional["StockRepository"] = None,
        newhigh_task: Optional[NewHighRefreshTask] = None,
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
                            data=[
                                self._format_item(it, "is_historical_newhigh")
                                for it in db_items[: self._MAX_ITEMS]
                            ],
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

        # 2차: In-Memory 캐시 (비어 있으면 get_newhigh_cache가 갱신을 트리거한다)
        cache = await task.get_newhigh_cache(self._MAX_ITEMS)
        if cache:
            return ResCommonResponse(
                rt_cd="0",
                msg1="성공",
                data=[self._format_item(it, "is_historical_new_high") for it in cache],
            )

        # 캐시가 비어 수집이 진행/예약된 상태 — 빈 목록으로 응답
        return ResCommonResponse(rt_cd="0", msg1="수집 중", data=[])

    @staticmethod
    def _format_item(item: dict, hist_key: str) -> dict:
        """DB row / 캐시 dict를 웹 응답 형식으로 변환한다.

        DB와 캐시는 역사적 신고가 키 이름이 다르므로(hist_key) 인자로 받는다.
        """
        price = item.get("current_price")
        rate = item.get("change_rate")
        return {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "stck_prpr": str(price if price is not None else 0),
            "prdy_ctrt": str(rate if rate is not None else 0),
            "rs_rating": item.get("rs_rating") or 0,
            "market_cap": item.get("market_cap") or 0,
            "trading_value": item.get("trading_value") or 0,
            "w52_high": item.get("w52_high") or 0,
            "is_historical_new_high": bool(item.get(hist_key)),
            "minervini_stage": item.get("minervini_stage") or 0,
        }
