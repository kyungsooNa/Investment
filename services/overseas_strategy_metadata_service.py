"""해외 장중 전략의 알고리즘 버전과 설정 cohort 식별자를 만든다."""
from __future__ import annotations

from common.config_hashing import compute_config_hash
from services.overseas_intraday_buyable_gap_up_service import OverseasIntradayBuyableGapUpService
from services.overseas_intraday_channel_breakout_service import OverseasIntradayChannelBreakoutService
from services.overseas_intraday_pocket_pivot_service import OverseasIntradayPocketPivotService
from services.overseas_intraday_rsi2_service import OverseasIntradayRSI2Service
from services.overseas_intraday_squeeze_breakout_service import OverseasIntradaySqueezeBreakoutService
from services.overseas_intraday_vbo_service import OverseasIntradayVBOService


def build_overseas_intraday_strategy_metadata(overseas_stock_cfg) -> dict[str, dict[str, str]]:
    """공유 주문 서비스가 전략별 실제 설정 cohort 를 기록할 메타데이터를 반환한다."""
    strategy_configs = (
        (
            OverseasIntradayVBOService,
            {
                "intraday_vbo": getattr(overseas_stock_cfg, "intraday_vbo", None),
                "vbo_macd_filter": getattr(overseas_stock_cfg, "vbo_macd_filter", None),
            },
        ),
        (OverseasIntradayChannelBreakoutService, getattr(overseas_stock_cfg, "intraday_channel_breakout", None)),
        (OverseasIntradayRSI2Service, getattr(overseas_stock_cfg, "intraday_rsi2", None)),
        (OverseasIntradayBuyableGapUpService, getattr(overseas_stock_cfg, "intraday_buyable_gap_up", None)),
        (OverseasIntradaySqueezeBreakoutService, getattr(overseas_stock_cfg, "intraday_squeeze_breakout", None)),
        (OverseasIntradayPocketPivotService, getattr(overseas_stock_cfg, "intraday_pocket_pivot", None)),
    )
    return {
        service_cls.STRATEGY_NAME: {
            "config_hash": compute_config_hash(config),
            "strategy_version": service_cls.STRATEGY_VERSION,
        }
        for service_cls, config in strategy_configs
    }
