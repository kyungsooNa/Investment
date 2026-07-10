"""당일 랭킹 데이터 기반 주도 테마 리포트 서비스."""
import logging
import math
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

    MIN_LIQUID_LEADER_TRADING_VALUE_WON = 1_000_000_000  # 10억
    MIN_LIQUID_THEME_TRADING_VALUE_WON = 10_000_000_000  # 100억
    MIN_LIQUID_MEMBER_COUNT = 2

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

                momentum_leaders = sorted(
                    scored,
                    key=lambda item: (item["change_rate"], item["trading_value_won"]),
                    reverse=True,
                )
                liquid_members = [
                    item for item in momentum_leaders
                    if item["trading_value_won"] >= self.MIN_LIQUID_LEADER_TRADING_VALUE_WON
                ]
                leaders = liquid_members[:leader_count]
                trading_value_sum = sum(item["trading_value_won"] for item in scored)
                trading_value_concentration_ratio = (
                    max(item["trading_value_won"] for item in scored) / trading_value_sum * 100
                    if trading_value_sum else 0.0
                )
                fi_net_sum = sum(item["fi_net_buy_won"] for item in scored)
                program_net_sum = sum(item["program_net_buy_won"] for item in scored)
                advance_count = sum(1 for item in scored if item["change_rate"] > 0)
                flow_ratio = (
                    round(((fi_net_sum + program_net_sum) / trading_value_sum) * 100, 2)
                    if trading_value_sum else 0.0
                )
                leader_avg_change_rate = round(
                    sum(item["change_rate"] for item in leaders) / len(leaders), 2
                ) if leaders else 0.0
                score_info = self._build_theme_score(
                    scored=scored,
                    leader_avg_change_rate=leader_avg_change_rate,
                    advancing_ratio=round(advance_count / len(scored) * 100, 1),
                    trading_value_sum_won=trading_value_sum,
                    flow_ratio=flow_ratio,
                    trading_value_concentration_ratio=trading_value_concentration_ratio,
                )
                liquidity_bonus = self._build_liquidity_bonus(
                    trading_value_sum,
                    leader_avg_change_rate,
                )
                market_leadership_score = round(score_info["theme_score"] + liquidity_bonus, 2)

                themes.append({
                    "normalized_name": normalized_name,
                    "sources": group.get("sources", []),
                    "report_date": report_date,
                    "scored_member_count": len(scored),
                    "liquid_member_count": len(liquid_members),
                    "is_liquid_theme": (
                        trading_value_sum >= self.MIN_LIQUID_THEME_TRADING_VALUE_WON
                        and len(liquid_members) >= self.MIN_LIQUID_MEMBER_COUNT
                    ),
                    "leader_avg_change_rate": leader_avg_change_rate,
                    "advance_count": advance_count,
                    "advancing_ratio": score_info["advancing_ratio"],
                    "trading_value_sum_won": trading_value_sum,
                    "trading_value_concentration_ratio": round(trading_value_concentration_ratio, 2),
                    "fi_net_buy_won": fi_net_sum,
                    "program_net_buy_won": program_net_sum,
                    "flow_ratio": flow_ratio,
                    "value_weighted_change_rate": score_info["value_weighted_change_rate"],
                    "zero_trading_value_ratio": score_info["zero_trading_value_ratio"],
                    "negative_trading_value_ratio": score_info["negative_trading_value_ratio"],
                    "theme_score": score_info["theme_score"],
                    "momentum_score": score_info["theme_score"],
                    "liquidity_bonus": liquidity_bonus,
                    "market_leadership_score": market_leadership_score,
                    "leaders": leaders,
                    "momentum_leaders": momentum_leaders[:leader_count],
                })

            themes.sort(
                key=lambda item: (
                    item["is_liquid_theme"],
                    item["market_leadership_score"],
                    item["leader_avg_change_rate"],
                    item["advancing_ratio"],
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
    def _build_theme_score(
        scored: List[Dict[str, Any]],
        leader_avg_change_rate: float,
        advancing_ratio: float,
        trading_value_sum_won: int,
        flow_ratio: float,
        trading_value_concentration_ratio: float,
    ) -> Dict[str, float]:
        total_count = len(scored)
        zero_trading_value_count = sum(1 for item in scored if item["trading_value_won"] <= 0)
        negative_trading_value_sum = sum(
            item["trading_value_won"] for item in scored if item["change_rate"] < 0
        )
        if trading_value_sum_won > 0:
            value_weighted_change_rate = (
                sum(item["change_rate"] * item["trading_value_won"] for item in scored)
                / trading_value_sum_won
            )
            trading_value_score = min(math.log10(trading_value_sum_won / 100_000_000), 5.0)
            negative_trading_value_ratio = negative_trading_value_sum / trading_value_sum_won * 100
        else:
            value_weighted_change_rate = (
                sum(item["change_rate"] for item in scored) / total_count if total_count else 0.0
            )
            trading_value_score = 0.0
            negative_trading_value_ratio = 0.0

        zero_trading_value_ratio = (
            zero_trading_value_count / total_count * 100 if total_count else 0.0
        )
        clipped_flow_ratio = max(min(flow_ratio, 20.0), -20.0)
        concentration_penalty = max(trading_value_concentration_ratio - 70.0, 0.0) * 0.05
        theme_score = (
            leader_avg_change_rate * 0.45
            + value_weighted_change_rate * 0.20
            + advancing_ratio * 0.03
            + trading_value_score * 0.8
            + clipped_flow_ratio * 0.10
            - zero_trading_value_ratio * 0.05
            - negative_trading_value_ratio * 0.04
            - concentration_penalty
        )
        return {
            "advancing_ratio": advancing_ratio,
            "value_weighted_change_rate": round(value_weighted_change_rate, 2),
            "zero_trading_value_ratio": round(zero_trading_value_ratio, 2),
            "negative_trading_value_ratio": round(negative_trading_value_ratio, 2),
            "theme_score": round(theme_score, 2),
        }

    @classmethod
    def _build_liquidity_bonus(
        cls,
        trading_value_sum_won: int,
        leader_avg_change_rate: float,
    ) -> float:
        """시장 주도성 정렬용 거래대금 보너스. 저탄력 대형주는 보너스를 제한한다."""
        if trading_value_sum_won < cls.MIN_LIQUID_THEME_TRADING_VALUE_WON:
            return 0.0
        ratio = trading_value_sum_won / cls.MIN_LIQUID_THEME_TRADING_VALUE_WON
        cap = 1.5 if leader_avg_change_rate < 5.0 else 9.0
        return round(min(math.log10(ratio) * 2.0, cap), 2)

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
