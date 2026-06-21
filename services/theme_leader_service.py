# services/theme_leader_service.py
"""
테마/업종 그룹별 주도주(RS Rating 상위 종목)를 식별하는 서비스.

분류 데이터(StockClassificationRepository)와 이미 계산된 RS Rating(RSRatingRepository)을
join 하여, 각 그룹 안에서 RS 상위 종목을 주도주로 선정한다. 네트워크 호출 없이
저장된 데이터만 읽는 온디맨드 조회이며, 수집(스크래핑)은 별도 태스크가 담당한다.
"""
import logging
import statistics
from typing import Optional, TYPE_CHECKING

from common.types import ResCommonResponse, ErrorCode
from core.performance_profiler import PerformanceProfiler

if TYPE_CHECKING:
    from repositories.stock_classification_repository import StockClassificationRepository
    from repositories.rs_rating_repository import RSRatingRepository


class ThemeLeaderService:
    """그룹(테마/업종) 내 RS 상위 종목을 주도주로 반환하는 서비스."""

    def __init__(
        self,
        classification_repository: "StockClassificationRepository",
        rs_rating_repository: "RSRatingRepository",
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
    ):
        self._classification_repo = classification_repository
        self._rs_repo = rs_rating_repository
        self._logger = logger or logging.getLogger(__name__)
        self.pm = performance_profiler or PerformanceProfiler(enabled=False)

    async def get_theme_leaders(
        self,
        top_n: int = 5,
        category_types: tuple = ("theme",),
    ) -> ResCommonResponse:
        """그룹별 주도주 목록을 그룹 강도(RS 중앙값) 내림차순으로 반환한다.

        data = [{
            "normalized_name": str,
            "sources": [str, ...],
            "group_rs_median": float,
            "member_count": int,
            "leaders": [{"code", "name", "rs_rating", "sources"}, ...]
        }, ...]
        """
        t_start = self.pm.start_timer()
        try:
            groups = await self._classification_repo.get_groups(category_types)
            if not groups:
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="테마 데이터가 아직 수집되지 않았습니다.",
                    data=[],
                )

            trade_date = await self._rs_repo.get_latest_date()
            rs_map = await self._rs_repo.get_by_date(trade_date) if trade_date else {}
            if not rs_map:
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="RS Rating 데이터가 아직 없습니다.",
                    data=[],
                )

            result = []
            for normalized_name, group in groups.items():
                scored = [
                    (member, rs_map[member["code"]])
                    for member in group["members"]
                    if member["code"] in rs_map
                ]
                if not scored:
                    continue
                scored.sort(key=lambda x: x[1], reverse=True)
                leaders = [
                    {
                        "code": member["code"],
                        "name": member["name"],
                        "rs_rating": rs,
                        "sources": member["sources"],
                    }
                    for member, rs in scored[:top_n]
                ]
                rs_values = [rs for _, rs in scored]
                result.append({
                    "normalized_name": normalized_name,
                    "sources": group["sources"],
                    "group_rs_median": float(statistics.median(rs_values)),
                    "member_count": len(scored),
                    "leaders": leaders,
                })

            result.sort(key=lambda g: g["group_rs_median"], reverse=True)

            self.pm.log_timer("ThemeLeaderService.get_theme_leaders", t_start,
                              extra_info=f"groups={len(result)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1=f"성공 ({len(result)}개 그룹)",
                data=result,
            )
        except Exception as e:
            self._logger.exception(f"ThemeLeaderService.get_theme_leaders 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=str(e),
                data=None,
            )
