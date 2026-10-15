"""미국장 성장주 계열 장중 전략 조립."""
from __future__ import annotations

from common.overseas_types import OverseasExchange
from services.minervini_stage_service import MinerviniStageService
from services.overseas_intraday_minervini_stage2_service import (
    OverseasIntradayMinerviniStage2Service,
)
from services.overseas_intraday_pocket_pivot_service import OverseasIntradayPocketPivotService


class OverseasGrowthStrategyBootstrap:
    def __init__(self, context, overseas_stock_cfg) -> None:
        self._ctx = context
        self._cfg = overseas_stock_cfg

    def reset(self) -> None:
        self._ctx.overseas_intraday_pp_service = None
        self._ctx.overseas_intraday_minervini_stage2_service = None

    def append_enabled(self, services: list, common: dict, opts) -> None:
        pp_cfg = getattr(self._cfg, "intraday_pocket_pivot", None)
        if getattr(pp_cfg, "enabled", False):
            self._ctx.overseas_intraday_pp_service = OverseasIntradayPocketPivotService(
                **common, **opts(pp_cfg, OverseasIntradayPocketPivotService),
            )
            services.append(self._ctx.overseas_intraday_pp_service)

        minervini_cfg = getattr(self._cfg, "intraday_minervini_stage2", None)
        if getattr(minervini_cfg, "enabled", False):
            self._ctx.overseas_intraday_minervini_stage2_service = (
                OverseasIntradayMinerviniStage2Service(
                    minervini_stage_service=MinerviniStageService(
                        stock_query_service=self._ctx.stock_query_service,
                        logger=self._ctx.logger,
                    ),
                    exchange=OverseasExchange(
                        getattr(self._cfg, "default_exchange", "NASD")
                    ),
                    **common,
                    **opts(minervini_cfg, OverseasIntradayMinerviniStage2Service),
                )
            )
            services.append(self._ctx.overseas_intraday_minervini_stage2_service)
