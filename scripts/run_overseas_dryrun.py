"""해외 VBO dry-run 단발 러너 — would-be 신호 누적 전용 (실주문 없음).

웹앱/스케줄러/스트리밍/주문 경로를 띄우지 않고, `OverseasVBODryRunService.scan_dry_run()`
을 1회 실행해 would-be BUY 신호를 shadow 저널(`logs/strategies/event_shadow/<date>.jsonl`,
`signal_source="overseas_dryrun"`)에 flush 한다.

**실주문 불가(구조적)**: 이 러너가 만드는 `OverseasVBODryRunService` 는 order_execution
의존을 갖지 않는다. 읽기 전용(일봉/현재가/잔고 FX) API만 호출한다.

권장 운용: 미국 정규장 마감(16:00 ET) 이후 매 거래일 1회 실행해 신호를 며칠 누적한 뒤
`scripts/analyze_overseas_dryrun.py` 로 would-be 성과를 집계한다(Phase 5 canary go/no-go).

사용:
    conda activate py310
    python scripts/run_overseas_dryrun.py --exchange NASD
    python scripts/run_overseas_dryrun.py --exchange NASD --date 20260622 --top-n 50
    python scripts/run_overseas_dryrun.py --exchange NASD --paper   # 모의 데이터 모드
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.overseas_types import OverseasExchange

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.broker_api_wrapper import BrokerAPIWrapper
from config.config_loader import load_configs
from core.cache.cache_store import CacheStore
from core.market_clock import MarketClock
from core.performance_profiler import PerformanceProfiler
from repositories.overseas_stock_code_repository import OverseasStockCodeRepository
from repositories.stock_code_repository import StockCodeRepository
from services.event_shadow_journal_service import EventShadowJournalService
from services.indicator_service import IndicatorService
from services.market_calendar_service import MarketCalendarService
from services.market_data_service import MarketDataService
from services.overseas_candidate_service import OverseasCandidateService
from services.overseas_position_sizing_service import (
    OverseasPositionSizingService,
    extract_fx_krw_per_usd,
)
from services.overseas_vbo_dryrun_service import OverseasVBODryRunService
from services.stock_query_service import StockQueryService


def _make_stdout_logger(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("overseas_dryrun_runner")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s  %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def resolve_exchange(value: str) -> OverseasExchange:
    """거래소 문자열을 OverseasExchange 로 해석한다(미지원 시 종료)."""
    try:
        return OverseasExchange(str(value).strip().upper())
    except ValueError:
        valid = ", ".join(e.value for e in OverseasExchange)
        raise SystemExit(f"[ERROR] 미지원 거래소: {value!r} (지원: {valid})")


async def build_dryrun_service(
    *,
    is_paper_trading: bool,
    slot_usd: float,
    max_qty: Optional[int],
    logger: logging.Logger,
):
    """dry-run 스캔에 필요한 최소 서비스 그래프를 조립한다(주문 경로 없음).

    Returns:
        (OverseasVBODryRunService, EventShadowJournalService, MarketClock(US))
    """
    config_data = load_configs()
    config_dict = config_data
    if hasattr(config_data, "model_dump"):
        config_dict = config_data.model_dump()
    elif hasattr(config_data, "dict"):
        config_dict = config_data.dict()

    # 국내 마감 계산용(서비스 의존). 트리거 시각 산출엔 us_clock 을 따로 쓴다.
    market_clock = MarketClock(
        market_open_time=config_dict.get("market_open_time", "09:00"),
        market_close_time=config_dict.get("market_close_time", "15:40"),
        timezone=config_dict.get("market_timezone", "Asia/Seoul"),
        logger=logger,
    )
    us_clock = MarketClock.for_us_equities(logger=logger)

    stock_code_repository = StockCodeRepository(logger=logger)
    mcs = MarketCalendarService(market_clock, logger)

    env = KoreaInvestApiEnv(config_dict, logger)
    env.set_trading_mode(is_paper_trading)
    if not await env.get_access_token():
        raise RuntimeError("토큰 발급 실패. config.yaml 의 API 키·계좌번호를 확인하세요.")
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

    overseas_stock_cfg = getattr(config_data, "overseas_stock", None)
    sizing_service = OverseasPositionSizingService(
        slot_usd=slot_usd if slot_usd is not None
        else getattr(overseas_stock_cfg, "dryrun_slot_usd", 1000.0),
        max_qty=max_qty if max_qty is not None
        else getattr(overseas_stock_cfg, "dryrun_max_qty", None),
        logger=logger,
    )
    candidate_service = OverseasCandidateService(
        overseas_stock_code_repository=OverseasStockCodeRepository(logger=logger),
        stock_query_service=stock_query_service,
        logger=logger,
    )
    journal = EventShadowJournalService(log_root="logs/strategies", logger=logger)

    async def _fx_provider():
        # KIS 해외 잔고(읽기 전용)에서 USD/KRW 환율 추출. 실패 시 None → KRW 생략.
        try:
            resp = await broker.get_overseas_balance()
        except Exception:
            return None
        return extract_fx_krw_per_usd(getattr(resp, "data", None))

    dryrun_service = OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=stock_query_service,
        shadow_journal=journal,
        logger=logger,
        position_sizing_service=sizing_service,
        fx_provider=_fx_provider,
    )
    return dryrun_service, journal, us_clock


async def run_scan(
    dryrun_service,
    journal,
    exchange: OverseasExchange,
    date_str: str,
    *,
    top_n: Optional[int],
    min_avg_trading_value: Optional[float],
    logger: logging.Logger,
) -> List[Dict[str, Any]]:
    """스캔 1회 실행 후 저널을 파일로 flush 한다(신호 0건이어도 flush 시도)."""
    signals = await dryrun_service.scan_dry_run(
        exchange,
        top_n=top_n,
        min_avg_trading_value=min_avg_trading_value,
        record=True,
    )
    path = journal.flush_to_file(date_str)
    logger.info(
        {"event": "overseas_dryrun_runner_done", "exchange": exchange.value,
         "date": date_str, "signals": len(signals or []), "flushed": str(path)}
    )
    return signals or []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="해외 VBO dry-run 단발 러너 (would-be 신호 누적, 실주문 없음)",
    )
    parser.add_argument("--exchange", default="NASD", help="NASD | NYSE | AMEX")
    parser.add_argument("--date", default=None,
                        help="flush 파일명 날짜 YYYYMMDD (미지정 시 미국장 현재 날짜)")
    parser.add_argument("--top-n", type=int, default=None, help="후보 상위 N개로 제한")
    parser.add_argument("--min-avg-trading-value", type=float, default=None,
                        help="후보 최소 평균 거래대금 필터")
    parser.add_argument("--slot-usd", type=float, default=None,
                        help="고정 USD 슬롯(미지정 시 config overseas_stock.dryrun_slot_usd)")
    parser.add_argument("--max-qty", type=int, default=None,
                        help="슬롯당 최대 수량 cap(미지정 시 config 값)")
    parser.add_argument("--paper", action="store_true",
                        help="모의 데이터 모드(기본 real, 읽기 전용)")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    logger = _make_stdout_logger()
    exchange = resolve_exchange(args.exchange)

    dryrun_service, journal, us_clock = await build_dryrun_service(
        is_paper_trading=args.paper,
        slot_usd=args.slot_usd,
        max_qty=args.max_qty,
        logger=logger,
    )
    date_str = args.date or us_clock.get_current_kst_date_str()
    signals = await run_scan(
        dryrun_service, journal, exchange, date_str,
        top_n=args.top_n, min_avg_trading_value=args.min_avg_trading_value,
        logger=logger,
    )
    print(f"[INFO] {exchange.value} dry-run 신호 {len(signals)}건 → "
          f"logs/strategies/event_shadow/{date_str}.jsonl")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
