"""당일 랭킹 데이터 기반 주도 테마 리포트 서비스."""
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from common.types import ErrorCode, ResCommonResponse
from core.performance_profiler import PerformanceProfiler

if TYPE_CHECKING:
    from repositories.stock_classification_repository import StockClassificationRepository


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


class ThemeDailyLeaderService:
    """장마감 랭킹 캐시를 테마별로 묶어 당일 주도 테마를 산출한다."""

    def __init__(
        self,
        classification_repository: "StockClassificationRepository",
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
    ):
        self._classification_repo = classification_repository
        self._logger = logger or logging.getLogger(__name__)
        self.pm = performance_profiler or PerformanceProfiler(enabled=False)

    async def build_daily_theme_report(
        self,
        rankings: Dict[str, List[Dict]],
        report_date: str,
        top_themes: int = 10,
        leader_count: int = 3,
        min_members: int = 3,
        category_types: tuple = ("theme",),
    ) -> ResCommonResponse:
        """당일 테마 강도 목록을 반환한다.

        외국인/기관 순매수대금은 기존 KIS 랭킹 데이터 관례에 맞춰 백만원 단위를
        원 단위로 환산하고, 프로그램 순매수대금은 원 단위로 취급한다.
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

            stock_map = self._build_stock_map(rankings.get("all_stocks") or [])
            if not stock_map:
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="당일 랭킹 데이터가 없습니다.",
                    data=[],
                )
            program_map = self._build_program_map(rankings.get("program_all_stocks") or [])

            themes = []
            for normalized_name, group in groups.items():
                scored = []
                for member in group.get("members", []):
                    code = member.get("code")
                    stock = stock_map.get(code)
                    if not stock:
                        continue
                    program_net = program_map.get(code, 0)
                    scored.append(self._build_member_score(member, stock, program_net))

                if len(scored) < min_members:
                    continue

                scored.sort(
                    key=lambda item: (item["change_rate"], item["trading_value_won"]),
                    reverse=True,
                )
                leaders = scored[:leader_count]
                trading_value_sum = sum(item["trading_value_won"] for item in scored)
                fi_net_sum = sum(item["fi_net_buy_won"] for item in scored)
                program_net_sum = sum(item["program_net_buy_won"] for item in scored)
                advance_count = sum(1 for item in scored if item["change_rate"] > 0)
                flow_ratio = (
                    round(((fi_net_sum + program_net_sum) / trading_value_sum) * 100, 2)
                    if trading_value_sum else 0.0
                )

                themes.append({
                    "normalized_name": normalized_name,
                    "sources": group.get("sources", []),
                    "report_date": report_date,
                    "scored_member_count": len(scored),
                    "leader_avg_change_rate": round(
                        sum(item["change_rate"] for item in leaders) / len(leaders), 2
                    ),
                    "advance_count": advance_count,
                    "advancing_ratio": round(advance_count / len(scored) * 100, 1),
                    "trading_value_sum_won": trading_value_sum,
                    "fi_net_buy_won": fi_net_sum,
                    "program_net_buy_won": program_net_sum,
                    "flow_ratio": flow_ratio,
                    "leaders": leaders,
                })

            themes.sort(
                key=lambda item: (
                    item["leader_avg_change_rate"],
                    item["advancing_ratio"],
                    item["flow_ratio"],
                    item["trading_value_sum_won"],
                ),
                reverse=True,
            )
            result = themes[:top_themes]
            self.pm.log_timer(
                "ThemeDailyLeaderService.build_daily_theme_report",
                t_start,
                extra_info=f"themes={len(result)}",
            )
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1=f"성공 ({len(result)}개 테마)",
                data=result,
            )
        except Exception as e:
            self._logger.exception(f"ThemeDailyLeaderService.build_daily_theme_report 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=str(e),
                data=None,
            )

    @staticmethod
    def _build_stock_map(stocks: List[Any]) -> Dict[str, Any]:
        out = {}
        for stock in stocks:
            code = (
                _get(stock, "stck_shrn_iscd")
                or _get(stock, "mksc_shrn_iscd")
                or _get(stock, "iscd")
                or _get(stock, "code")
            )
            if code:
                out[str(code)] = stock
        return out

    @staticmethod
    def _build_program_map(program_stocks: List[Any]) -> Dict[str, int]:
        out = {}
        for stock in program_stocks:
            code = (
                _get(stock, "stck_shrn_iscd")
                or _get(stock, "mksc_shrn_iscd")
                or _get(stock, "iscd")
                or _get(stock, "code")
            )
            if code:
                out[str(code)] = _to_int(_get(stock, "whol_smtn_ntby_tr_pbmn"))
        return out

    @staticmethod
    def _build_member_score(member: Dict[str, Any], stock: Any, program_net_won: int) -> Dict[str, Any]:
        code = member.get("code", "")
        name = _get(stock, "hts_kor_isnm") or _get(stock, "name") or member.get("name", "")
        frgn_pbmn_mil = _to_float(_get(stock, "frgn_ntby_tr_pbmn"))
        orgn_pbmn_mil = _to_float(_get(stock, "orgn_ntby_tr_pbmn"))
        fi_net_won = int(round((frgn_pbmn_mil + orgn_pbmn_mil) * 1_000_000))
        return {
            "code": code,
            "name": name,
            "sources": member.get("sources", []),
            "current_price": _to_int(_get(stock, "stck_prpr")),
            "change_rate": _to_float(_get(stock, "prdy_ctrt")),
            "trading_value_won": _to_int(_get(stock, "acml_tr_pbmn")),
            "fi_net_buy_won": fi_net_won,
            "program_net_buy_won": program_net_won,
        }
