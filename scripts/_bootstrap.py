"""CLI 진단 도구용 최소 서비스 그래프 부트스트랩.

WebAppContext 전체(스케줄러/태스크/스트리밍 포함)를 띄우지 않고
전략 디버깅에 필요한 의존성만 최소로 초기화한다.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional, Tuple

from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.broker_api_wrapper import BrokerAPIWrapper
from core.market_clock import MarketClock
from core.cache.cache_store import CacheStore
from core.performance_profiler import PerformanceProfiler
from repositories.stock_code_repository import StockCodeRepository
from services.market_calendar_service import MarketCalendarService
from services.market_data_service import MarketDataService
from services.indicator_service import IndicatorService
from services.stock_query_service import StockQueryService
from services.oneil_universe_service import OneilUniverseService


def make_stdout_logger(name: str = "debug_script", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s  %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


async def bootstrap_pp_strategy(
    is_paper_trading: bool = True,
    logger: Optional[logging.Logger] = None,
) -> Tuple[StockQueryService, OneilUniverseService, MarketClock]:
    """PP 전략 디버깅에 필요한 최소 서비스 그래프를 생성하고 반환한다.

    Returns:
        (StockQueryService, OneilUniverseService, MarketClock)
    """
    if logger is None:
        logger = make_stdout_logger()

    config_data = load_configs()
    config_dict = config_data
    if hasattr(config_data, "model_dump"):
        config_dict = config_data.model_dump()
    elif hasattr(config_data, "dict"):
        config_dict = config_data.dict()

    market_clock = MarketClock(
        market_open_time=config_dict.get("market_open_time", "09:00"),
        market_close_time=config_dict.get("market_close_time", "15:40"),
        timezone=config_dict.get("market_timezone", "Asia/Seoul"),
        logger=logger,
    )
    stock_code_repository = StockCodeRepository(logger=logger)
    mcs = MarketCalendarService(market_clock, logger)

    env = KoreaInvestApiEnv(config_dict, logger)
    env.set_trading_mode(is_paper_trading)
    token_ok = await env.get_access_token()
    if not token_ok:
        raise RuntimeError(
            "토큰 발급 실패. config.yaml의 API 키와 계좌번호를 확인하세요."
        )
    if is_paper_trading:
        await env.get_real_access_token()

    broker = BrokerAPIWrapper(
        env=env,
        logger=logger,
        market_clock=market_clock,
        market_calendar_service=mcs,
        stock_code_repository=stock_code_repository,
    )
    mcs.set_broker(broker)

    pm = PerformanceProfiler(enabled=False)
    cache_store = CacheStore(config_dict)
    cache_store.set_logger(logger)

    market_data_service = MarketDataService(
        broker_api_wrapper=broker,
        env=env,
        logger=logger,
        market_clock=market_clock,
        cache_store=cache_store,
        market_calendar_service=mcs,
        performance_profiler=pm,
    )
    indicator_service = IndicatorService(cache_store=cache_store, performance_profiler=pm)
    stock_query_service = StockQueryService(
        market_data_service=market_data_service,
        logger=logger,
        market_clock=market_clock,
        indicator_service=indicator_service,
    )
    universe_service = OneilUniverseService(
        stock_query_service=stock_query_service,
        indicator_service=indicator_service,
        stock_code_repository=stock_code_repository,
        market_clock=market_clock,
        logger=logger,
        performance_profiler=pm,
    )

    return stock_query_service, universe_service, market_clock
