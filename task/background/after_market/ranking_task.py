# task/background/ranking_task.py
"""
랭킹 데이터 수집 및 캐시 관리 태스크.
전체 종목 순회가 필요한 랭킹 집계(외국인/기관/개인 순매수 등)와
장마감 후 기본 랭킹 캐시를 관리한다.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, TYPE_CHECKING

from brokers.broker_api_wrapper import BrokerAPIWrapper
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from common.types import ResCommonResponse, ErrorCode
from core.market_clock import MarketClock
from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskState
from services.market_calendar_service import MarketCalendarService
from repositories.stock_code_repository import StockCodeRepository
from core.performance_profiler import PerformanceProfiler
from services.telegram_notifier import TelegramReporter
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel
from scheduler.worker.worker_pool import WorkerPool


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


# ETF/ETN 브랜드명 접두사 (TradingService._ETF_PREFIXES 와 동일)
_ETF_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
    "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
    "VITA", "TREX", "MASTER", "WOORI", "KINDEX", "RISE",
)


class RankingTask(AfterMarketTask):
    """랭킹 데이터를 수집·캐시하는 백그라운드 태스크."""

    # 청크 크기 (API 호출 페이싱은 ApiBudgetLimiter가 중앙에서 담당)
    API_CHUNK_SIZE = 8
    PERIOD_RANKING_ALLOWED_DAYS = {1, 3, 5, 10, 20}
    PERIOD_RANKING_ALLOWED_METRICS = {"amount", "qty"}
    DEFAULT_PERIOD_RANKING_DAYS = 5

    def __init__(
        self,
        broker_api_wrapper: BrokerAPIWrapper,
        stock_code_repository: StockCodeRepository,
        env: KoreaInvestApiEnv = None,
        logger=None,
        market_clock: MarketClock = None,
        market_data_service=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        telegram_reporter: Optional[TelegramReporter] = None,
        market_calendar_service: Optional[MarketCalendarService] = None,
        worker_pool: Optional[WorkerPool] = None,
        stock_classification_repository=None,
        period_ranking_repository=None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._broker = broker_api_wrapper
        self.stock_code_repository = stock_code_repository
        self._env = env
        self._market_data_service = market_data_service
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._notification_service = notification_service
        self._telegram_reporter = telegram_reporter
        self._stock_classification_repository = stock_classification_repository
        self._period_ranking_repository = period_ranking_repository
        self._suspend_event: asyncio.Event = asyncio.Event()
        self._suspend_event.set()  # 초기에는 실행 가능 상태

        # 투자자별 순매수 랭킹 캐시
        self._foreign_net_buy_cache: List[Dict] = []
        self._foreign_net_sell_cache: List[Dict] = []
        self._inst_net_buy_cache: List[Dict] = []
        self._inst_net_sell_cache: List[Dict] = []
        self._prsn_net_buy_cache: List[Dict] = []
        self._prsn_net_sell_cache: List[Dict] = []
        self._trading_value_cache: List[Dict] = []  # 거래대금 랭킹 (투자자 데이터 기반)
        # 프로그램 매매 랭킹 캐시
        self._program_net_buy_cache: List[Dict] = []
        self._program_net_sell_cache: List[Dict] = []
        self._daily_theme_report_rankings: Dict[str, List[Dict] | str] = {}
        self._investor_ranking_updated_at: Optional[datetime] = None
        self._is_refreshing: bool = False
        self._last_collected_date: Optional[str] = None
        self._period_ranking_cache: Dict[tuple[str, int], List[Dict]] = {}
        self._period_ranking_tasks: Dict[tuple[str, int], asyncio.Task] = {}
        self._period_ranking_intraday_keys: set[tuple[str, int]] = set()

        # 기본 랭킹 캐시 (상승/하락/거래량/거래대금) — 장마감 후 1회
        self._basic_ranking_cache: Dict[str, ResCommonResponse] = {}
        self._basic_ranking_updated_at: Optional[datetime] = None
        self._basic_last_collected_date: Optional[str] = None

        # 진행률 상태
        self._progress: Dict = {
            "running": False,
            "processed": 0,
            "total": 0,
            "collected": 0,
            "elapsed": 0.0,
        }

    # ── SchedulableTask 인터페이스 구현 ────────────────────────

    @property
    def task_name(self) -> str:
        return "ranking_refresh"

    @property
    def _scheduler_label(self) -> str:
        return "RankingTask"

    async def _on_start_hook(self) -> None:
        self._suspend_event.set()
        # 재시작 복구: TimeDispatcher는 거래일당 1회만 티켓을 발행하므로
        # 티켓 발행 후 재시작하면 당일 기간수급 캐시가 비어도 재예열이 없다.
        self._tasks.append(asyncio.create_task(self._period_ranking_self_heal()))

    async def suspend(self) -> None:
        """랭킹 수집을 일시 중지한다 (chunk 사이에서 대기)."""
        if self._state == TaskState.RUNNING:
            self._suspend_event.clear()
            self._state = TaskState.SUSPENDED
            self._logger.info("RankingTask 일시 중지")

    async def resume(self) -> None:
        """일시 중지된 랭킹 수집을 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._suspend_event.set()
            self._state = TaskState.RUNNING
            self._logger.info("RankingTask 재개")

    async def execute(self, payload: dict) -> None:
        """WorkerPool 핸들러 — Ticket의 payload를 받아 랭킹 갱신을 수행한다.

        멱등성 보장: 동일 날짜에 기본·투자자 랭킹이 모두 수집되어 있으면 즉시 반환.
        """
        date: str = payload.get("date", "")
        self._invalidate_intraday_period_cache()
        needs_basic = not self._basic_last_collected_date or self._basic_last_collected_date != date
        needs_investor = not self._last_collected_date or self._last_collected_date != date
        needs_period = (date, self.DEFAULT_PERIOD_RANKING_DAYS) not in self._period_ranking_cache

        if not needs_basic and not needs_investor and not needs_period:
            self._logger.info(f"RankingTask execute: {date} 이미 완료 — 스킵")
            return

        self._logger.info(f"RankingTask execute: {date} 갱신 시작")
        async with self._running_state():
            if needs_basic:
                await self.refresh_basic_ranking()
                self._basic_last_collected_date = date
            if needs_investor:
                await self.refresh_investor_ranking()
                self._last_collected_date = date
            if needs_period:
                await self.prewarm_period_ranking(date)

    # ── 장마감 후 자동 갱신 스케줄러 ────────────────────────────

    async def start_after_market_scheduler(self) -> None:
        """장마감 후 자동으로 랭킹 갱신을 스케줄링하는 루프."""
        await self._after_market_scheduler()

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백: 해당 거래일의 랭킹 갱신이 필요하면 실행."""
        self._invalidate_intraday_period_cache()
        needs_basic = (
            not self._basic_last_collected_date
            or self._basic_last_collected_date != latest_trading_date
        )
        needs_investor = (
            not self._last_collected_date
            or self._last_collected_date != latest_trading_date
        )
        needs_period = (
            latest_trading_date,
            self.DEFAULT_PERIOD_RANKING_DAYS,
        ) not in self._period_ranking_cache

        if needs_basic:
            await self.refresh_basic_ranking()
            self._basic_last_collected_date = latest_trading_date
        if needs_investor:
            await self.refresh_investor_ranking()
            self._last_collected_date = latest_trading_date
        if needs_period:
            await self.prewarm_period_ranking(latest_trading_date)

    # ── 기본 랭킹 캐시 (상승/하락/거래량/거래대금) ───────────────

    async def refresh_basic_ranking(self) -> None:
        """상승률/하락률/거래량/거래대금 랭킹을 1회 조회하여 캐시."""
        if not self._market_data_service:
            self._logger.warning("MarketDataService 미설정 — 기본 랭킹 캐시 스킵")
            return

        async with self._running_state():
            t_start = self.pm.start_timer()
            self._logger.info("기본 랭킹 캐시 갱신 시작 (상승/하락/거래량/거래대금)")
            try:
                rise_resp, fall_resp, vol_resp, tv_resp = await asyncio.gather(
                    self._market_data_service.get_top_rise_fall_stocks(True),
                    self._market_data_service.get_top_rise_fall_stocks(False),
                    self._market_data_service.get_top_volume_stocks(),
                    self._market_data_service.get_top_trading_value_stocks(),
                    return_exceptions=True,
                )
                for key, resp in [("rise", rise_resp), ("fall", fall_resp),
                                  ("volume", vol_resp), ("trading_value", tv_resp)]:
                    if isinstance(resp, Exception):
                        self._logger.error(f"기본 랭킹 '{key}' 조회 실패: {resp}")
                    else:
                        self._basic_ranking_cache[key] = resp

                self._basic_ranking_updated_at = datetime.now()
                self._logger.info(f"기본 랭킹 캐시 갱신 완료: {list(self._basic_ranking_cache.keys())}")
                self.pm.log_timer("RankingTask.refresh_basic_ranking", t_start, threshold=1.0)
                if self._notification_service:
                    await self._notification_service.emit(
                        NotificationCategory.BACKGROUND, NotificationLevel.INFO, "기본 랭킹 갱신 완료",
                        f"상승/하락/거래량/거래대금 캐시 갱신 완료",
                    )
            except Exception as e:
                self._logger.error(f"기본 랭킹 캐시 갱신 실패: {e}", exc_info=True)
                if self._notification_service:
                    await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "기본 랭킹 갱신 실패", str(e))

    def get_progress(self) -> Dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현)."""
        return dict(self._progress)

    def get_investor_ranking_progress(self) -> Dict:
        """투자자 랭킹 수집 진행률 반환."""
        return self.get_progress()

    def get_daily_theme_report_rankings(self) -> Dict:
        """당일 주도 테마 리포트용 랭킹 원천 데이터를 반환한다."""
        result: Dict = {}
        for key, value in self._daily_theme_report_rankings.items():
            if isinstance(value, list):
                result[key] = [dict(item) for item in value]
            else:
                result[key] = value
        return result

    def get_basic_ranking_cache(self, category: str) -> Optional[ResCommonResponse]:
        """장마감 후 캐시된 기본 랭킹 반환. 캐시 없으면 None."""
        return self._basic_ranking_cache.get(category)

    # ── 투자자별 순매수/순매도 랭킹 ────────────────────────────

    async def _fetch_with_retry(self, api_call, *args, **kwargs):
        """API 호출을 재시도 로직으로 감싸는 헬퍼."""
        t_start = self.pm.start_timer()
        max_retries = 3
        delay = 1.0  # 초
        for attempt in range(max_retries):
            try:
                resp = await api_call(*args, **kwargs)
                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                    self.pm.log_timer(f"RankingTask._fetch_with_retry({api_call.__name__}, {args[0]})", t_start)
                    return resp

                error_msg = resp.msg1 if resp else "응답 없음"
                self._logger.warning(
                    f"API 호출 실패 (시도 {attempt + 1}/{max_retries}): {api_call.__name__}({args[0]}), 사유: {error_msg}. {delay}초 후 재시도."
                )
            except Exception as e:
                self._logger.error(
                    f"API 호출 예외 (시도 {attempt + 1}/{max_retries}): {api_call.__name__}({args[0]}), 오류: {e}. {delay}초 후 재시도.",
                    exc_info=True
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 1.5  # 약간의 지수 백오프

        self._logger.error(f"API 호출 최종 실패: {api_call.__name__}({args[0]})")
        self.pm.log_timer(f"RankingTask._fetch_with_retry({api_call.__name__}, {args[0]}) [최종실패]", t_start)
        return None  # 최종 실패 시 None 반환

    async def refresh_investor_ranking(self, force: bool = False) -> None:
        """전체 종목을 순회하여 외국인/기관/개인 순매수/순매도 랭킹을 갱신한다."""
        # [성능 보호] 장 중에는 실행하지 않음
        if self._mcs and await self._mcs.is_market_open_now():
            self._logger.info("장 운영 중이므로 투자자 랭킹 전체 갱신을 건너뜁니다.")
            return

        if self._is_refreshing:
            self._logger.info("투자자 랭킹 갱신 이미 진행 중 — 스킵")
            return

        t_start_total = self.pm.start_timer()
        self._is_refreshing = True
        start_time = time.time()
        self._logger.info("투자자 랭킹 백그라운드 갱신 시작")

        # [변경] 오늘 날짜 대신 실제 장이 열린 최근 날짜 조회
        target_date = None
        if self._mcs:
            target_date = await self._mcs.get_latest_trading_date()

        if not target_date:
            self._logger.error("최근 거래일을 확인할 수 없어 투자자 랭킹 갱신을 중단합니다.")
            self._is_refreshing = False
            return

        if not force and self._last_collected_date == target_date:
            self._logger.info(f"이미 {target_date} 투자자 랭킹 갱신 완료 — 스킵")
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.INFO, "투자자 랭킹 갱신 스킵",
                    f"{target_date} 이미 갱신 완료된 상태입니다."
                )
            self._is_refreshing = False
            return

        self._logger.info(f"투자자 랭킹 백그라운드 갱신 시작 (기준일: {target_date})")
        self._progress = {"running": True, "processed": 0, "total": 0, "collected": 0, "elapsed": 0.0}

        try:
            # 1. 전체 종목 로드
            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            self._progress["total"] = total
            self._logger.info(f"투자자 랭킹: 전체 {total}개 종목 순회 시작")

            # 2. 종목별 투자자 매매동향 + 프로그램매매추이 조회
            results: List[Dict] = []
            program_results: List[Dict] = []
            processed = 0

            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                # suspend 상태이면 resume될 때까지 대기
                await self._suspend_event.wait()

                # 투자자 매매동향 + 프로그램매매추이 동시 호출
                investor_tasks = [
                    self._fetch_with_retry(self._broker.get_investor_trade_by_stock_daily, code, target_date)
                    for code, _, _ in chunk
                ]
                program_tasks = [
                    self._fetch_with_retry(self._broker.get_program_trade_by_stock_daily, code, target_date)
                    for code, _, _ in chunk
                ]
                all_responses = await asyncio.gather(
                    *investor_tasks, *program_tasks, return_exceptions=True
                )
                investor_responses = all_responses[:len(chunk)]
                program_responses = all_responses[len(chunk):]

                for (code, name, market), resp in zip(chunk, investor_responses):
                    if isinstance(resp, Exception):
                        continue
                    if not resp:
                        continue
                    data = resp.data
                    if not data:
                        continue
                    # 캐시 역직렬화 시 dataclass로 변환될 수 있으므로 dict로 통일
                    if hasattr(data, 'to_dict') and callable(data.to_dict):
                        data = data.to_dict()
                    if not isinstance(data, dict):
                        continue

                    frgn_qty = int(data.get("frgn_ntby_qty", "0") or "0")
                    orgn_qty = int(data.get("orgn_ntby_qty", "0") or "0")
                    prsn_qty = int(data.get("prsn_ntby_qty", "0") or "0")
                    frgn_pbmn = int(data.get("frgn_ntby_tr_pbmn", "0") or "0")
                    orgn_pbmn = int(data.get("orgn_ntby_tr_pbmn", "0") or "0")
                    prsn_pbmn = int(data.get("prsn_ntby_tr_pbmn", "0") or "0")

                    acml_tr_pbmn = data.get("acml_tr_pbmn", "0") or "0"

                    results.append({
                        "stck_shrn_iscd": code,
                        "hts_kor_isnm": name,
                        "stck_prpr": data.get("stck_prpr", "0"),
                        "prdy_ctrt": data.get("prdy_ctrt", "0"),
                        "prdy_vrss": data.get("prdy_vrss", "0"),
                        "prdy_vrss_sign": data.get("prdy_vrss_sign", ""),
                        "acml_vol": data.get("acml_vol", "0"),
                        "acml_tr_pbmn": acml_tr_pbmn,
                        "frgn_ntby_qty": str(frgn_qty),
                        "orgn_ntby_qty": str(orgn_qty),
                        "prsn_ntby_qty": str(prsn_qty),
                        "frgn_ntby_tr_pbmn": str(frgn_pbmn),
                        "orgn_ntby_tr_pbmn": str(orgn_pbmn),
                        "prsn_ntby_tr_pbmn": str(prsn_pbmn),
                    })

                # 프로그램매매추이 수집
                for (code, name, market), resp in zip(chunk, program_responses):
                    if isinstance(resp, Exception):
                        continue
                    if not resp:
                        continue
                    data = resp.data
                    if not data:
                        continue
                    if hasattr(data, 'to_dict') and callable(data.to_dict):
                        data = data.to_dict()
                    if not isinstance(data, dict):
                        continue

                    ntby_tr_pbmn = int(data.get("whol_smtn_ntby_tr_pbmn", "0") or "0")

                    program_results.append({
                        "stck_shrn_iscd": code,
                        "hts_kor_isnm": name,
                        "stck_prpr": data.get("stck_clpr", "0"),
                        "prdy_ctrt": data.get("prdy_ctrt", "0"),
                        "prdy_vrss": data.get("prdy_vrss", "0"),
                        "prdy_vrss_sign": data.get("prdy_vrss_sign", ""),
                        "acml_vol": data.get("acml_vol", "0"),
                        "acml_tr_pbmn": data.get("acml_tr_pbmn", "0") or "0",
                        "whol_smtn_ntby_tr_pbmn": str(ntby_tr_pbmn),
                        "whol_smtn_ntby_qty": data.get("whol_smtn_ntby_qty", "0") or "0",
                        "whol_smtn_seln_tr_pbmn": data.get("whol_smtn_seln_tr_pbmn", "0") or "0",
                        "whol_smtn_shnu_tr_pbmn": data.get("whol_smtn_shnu_tr_pbmn", "0") or "0",
                    })

                processed += len(chunk)
                elapsed = time.time() - start_time
                self._progress.update({
                    "processed": processed,
                    "collected": len(results),
                    "elapsed": round(elapsed, 1),
                })
                if processed % 50 == 0 or processed >= total:
                    self._logger.info(
                        f"투자자 랭킹 진행: {processed}/{total} ({processed/total*100:.1f}%) "
                        f"| 수집: {len(results)} | 프로그램: {len(program_results)} | 소요: {elapsed:.1f}s"
                    )

            # 2-1. 프로그램 데이터의 acml_tr_pbmn으로 투자자 결과 보정
            prog_tr_map = {r["stck_shrn_iscd"]: r["acml_tr_pbmn"] for r in program_results}
            for r in results:
                if int(r.get("acml_tr_pbmn", "0") or "0") == 0:
                    r["acml_tr_pbmn"] = prog_tr_map.get(r["stck_shrn_iscd"], "0")

            # 3. 투자자별 정렬 → 순매수대금 기준 상위 30 / 하위 30
            self._foreign_net_buy_cache, self._foreign_net_sell_cache = \
                self._build_ranking(results, "frgn_ntby_tr_pbmn")
            self._inst_net_buy_cache, self._inst_net_sell_cache = \
                self._build_ranking(results, "orgn_ntby_tr_pbmn")
            self._prsn_net_buy_cache, self._prsn_net_sell_cache = \
                self._build_ranking(results, "prsn_ntby_tr_pbmn")

            # 거래대금 랭킹도 함께 구축 (acml_tr_pbmn 기준 상위 30)
            self._trading_value_cache = self._build_trading_value_ranking(results, top_n=30)

            # 4. 프로그램 순매수대금 정렬 → 상위 30 / 하위 30
            self._program_net_buy_cache, self._program_net_sell_cache = \
                self._build_ranking(program_results, "whol_smtn_ntby_tr_pbmn")

            self._investor_ranking_updated_at = datetime.now()
            self._last_collected_date = target_date

            elapsed = time.time() - start_time
            self._logger.info(
                f"투자자 랭킹 갱신 완료: {len(results)}개 종목 수집, 소요: {elapsed:.1f}s"
            )
            self.pm.log_timer("RankingTask.refresh_investor_ranking", t_start_total, threshold=10.0)
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.INFO, "투자자 랭킹 갱신 완료",
                    f"{len(results)}개 종목 수집, 소요: {elapsed:.1f}초",
                )
            rankings_for_report = {
                'foreign_buy': self._foreign_net_buy_cache,
                'foreign_sell': self._foreign_net_sell_cache,
                'inst_buy': self._inst_net_buy_cache,
                'inst_sell': self._inst_net_sell_cache,
                'prsn_buy': self._prsn_net_buy_cache,
                'prsn_sell': self._prsn_net_sell_cache,
                'program_buy': self._program_net_buy_cache,
                'program_sell': self._program_net_sell_cache,
                'trading_value': self._trading_value_cache,
                'all_stocks': results,
                'program_all_stocks': program_results
            }
            self._daily_theme_report_rankings = {
                key: [dict(item) for item in value] if isinstance(value, list) else value
                for key, value in rankings_for_report.items()
            }
            self._daily_theme_report_rankings["report_date"] = target_date
            if self._telegram_reporter:
                self._logger.info("텔레그램 랭킹 리포트 전송 시작")
                try:
                    await self._telegram_reporter.send_ranking_report(rankings_for_report, report_date=target_date)
                    self._logger.info("텔레그램 랭킹 리포트 전송 완료")
                except Exception as e:
                    self._logger.error(f"텔레그램 랭킹 리포트 전송 중 오류: {e}", exc_info=True)
        except Exception as e:
            self._logger.error(f"투자자 랭킹 갱신 실패: {e}", exc_info=True)
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "투자자 랭킹 갱신 실패", str(e))
        finally:
            self._is_refreshing = False
            self._progress["running"] = False

    @staticmethod
    def _build_ranking(results: List[Dict], pbmn_field: str, top_n: int = 30):
        """순매수대금 필드 기준 정렬 → (상위 30, 하위 30) 튜플 반환."""
        sorted_list = sorted(results, key=lambda x: int(x[pbmn_field]), reverse=True)

        buy_top = [dict(item) for item in sorted_list[:top_n]]
        for i, item in enumerate(buy_top, 1):
            item["data_rank"] = str(i)

        sell_slice = sorted_list[-top_n:] if len(sorted_list) >= top_n else sorted_list[:]
        sell_top = [dict(item) for item in reversed(sell_slice)]
        for i, item in enumerate(sell_top, 1):
            item["data_rank"] = str(i)

        return buy_top, sell_top

    @staticmethod
    def _build_trading_value_ranking(results: List[Dict], top_n: int = 30) -> List[Dict]:
        """누적거래대금(acml_tr_pbmn) 기준 내림차순 상위 N개 반환."""
        sorted_list = sorted(results, key=lambda x: int(x.get("acml_tr_pbmn", "0") or "0"), reverse=True)
        top = [dict(item) for item in sorted_list[:top_n]]
        for i, item in enumerate(top, 1):
            item["data_rank"] = str(i)
        return top

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sum_rows(rows: List[Dict], field: str) -> int:
        return sum(RankingTask._to_int(row.get(field, 0)) for row in rows if isinstance(row, dict))

    async def _load_industry_map(self) -> Dict[str, str]:
        """분류 저장소에서 종목코드 -> 대표 업종명을 만든다. 데이터가 없으면 빈 dict."""
        repo = self._stock_classification_repository
        if repo is None:
            return {}

        try:
            groups = await repo.get_groups(category_types=("industry",))
        except Exception as e:
            self._logger.warning(f"업종 분류 조회 실패: {e}")
            return {}

        industry_map: Dict[str, str] = {}
        for industry, group in sorted((groups or {}).items(), key=lambda item: item[0]):
            members = group.get("members", []) if isinstance(group, dict) else []
            for member in members:
                if not isinstance(member, dict):
                    continue
                code = str(member.get("code") or "").strip()
                if code and code not in industry_map:
                    industry_map[code] = industry
        return industry_map

    async def get_period_investor_program_net_buy_ranking(
        self,
        days: int = 5,
        metric: str = "amount",
        limit: int = 30,
    ) -> ResCommonResponse:
        """최근 N거래일 외국인+기관+프로그램 순매수 기간 랭킹을 생성한다.

        amount 정렬은 외국인/기관 백만원 단위와 프로그램 원 단위를 원 단위로 통일한다.
        qty 정렬은 세 주체의 순매수량 합산 기준이다.
        """
        if days not in self.PERIOD_RANKING_ALLOWED_DAYS:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=f"days는 {sorted(self.PERIOD_RANKING_ALLOWED_DAYS)} 중 하나여야 합니다.",
                data=[],
            )
        if metric not in self.PERIOD_RANKING_ALLOWED_METRICS:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="metric은 amount 또는 qty 여야 합니다.",
                data=[],
            )

        target_date = None
        if self._mcs:
            target_date = await self._mcs.get_latest_trading_date()
        if not target_date:
            target_date = datetime.now().strftime("%Y%m%d")

        cache_key = (str(target_date), days)
        results = self._peek_period_ranking(cache_key)
        if results is None:
            self._trigger_period_ranking_collection(cache_key)
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1=f"최근 {days}거래일 기간수급 수집 중입니다. 완료되면 자동 갱신됩니다.",
                data=[],
            )

        sort_field = "combined_period_ntby_tr_pbmn_won" if metric == "amount" else "combined_period_ntby_qty"
        ranked_results = [
            dict(item, period_metric=metric, latest_trading_date=str(target_date))
            for item in results
            if self._to_int(item.get(sort_field)) > 0
        ]
        ranked_results.sort(key=lambda item: self._to_int(item.get(sort_field)), reverse=True)
        top = [dict(item) for item in ranked_results[:limit]]
        for i, item in enumerate(top, 1):
            item["data_rank"] = str(i)

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1=f"최근 {days}거래일 외국인+기관+프로그램 기간 순매수 랭킹 조회 성공",
            data=top,
        )

    async def prewarm_period_ranking(
        self,
        target_date: str,
        days: int = DEFAULT_PERIOD_RANKING_DAYS,
    ) -> None:
        """장 마감 배치의 유휴 구간에서 기본 기간수급 캐시를 미리 생성한다."""
        cache_key = (str(target_date), days)
        if cache_key in self._period_ranking_cache:
            return
        self._logger.info(f"기간수급 랭킹 캐시 예열 시작: {target_date}, {days}일")
        await self._get_or_collect_period_ranking(cache_key)
        if cache_key in self._period_ranking_cache:
            self._logger.info(f"기간수급 랭킹 캐시 예열 완료: {target_date}, {days}일")
        else:
            self._logger.warning(f"기간수급 랭킹 캐시 예열 미완료: {target_date}, {days}일")

    async def _period_ranking_self_heal(self) -> None:
        """시작 시 당일 기간수급 캐시가 없으면 DB 복원 또는 예열한다 (장중 제외)."""
        try:
            if self._mcs is None:
                return
            if await self._mcs.is_market_open_now():
                return
            target_date = await self._mcs.get_latest_trading_date()
            if not target_date:
                return
            cache_key = (str(target_date), self.DEFAULT_PERIOD_RANKING_DAYS)
            if cache_key in self._period_ranking_cache:
                return
            async with self._running_state():
                await self.prewarm_period_ranking(str(target_date))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.warning(f"기간수급 self-heal 실패: {e}")

    def _peek_period_ranking(self, cache_key: tuple[str, int]) -> Optional[List[Dict]]:
        """메모리 → DB 순으로 즉시 반환 가능한 기간수급 결과를 찾는다."""
        results = self._period_ranking_cache.get(cache_key)
        if results is not None:
            return results
        stored = self._load_period_ranking_from_db(cache_key)
        if stored:
            self._logger.info(f"기간수급 랭킹 DB 복원: {cache_key[0]}, {cache_key[1]}일")
            self._period_ranking_cache[cache_key] = stored
            return stored
        return None

    def _trigger_period_ranking_collection(self, cache_key: tuple[str, int]) -> None:
        """기간수급 수집을 백그라운드로 시작한다 (이미 진행 중이면 no-op)."""
        if cache_key in self._period_ranking_tasks:
            return
        self._logger.info(
            f"기간수급 캐시 없음 → 온디맨드 백그라운드 수집 트리거: {cache_key[0]}, {cache_key[1]}일"
        )
        self._tasks = [t for t in self._tasks if not t.done()]
        self._tasks.append(
            asyncio.create_task(self._collect_period_ranking_in_background(cache_key))
        )

    async def _collect_period_ranking_in_background(self, cache_key: tuple[str, int]) -> None:
        try:
            async with self._running_state():
                await self._get_or_collect_period_ranking(cache_key)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.warning(f"기간수급 백그라운드 수집 실패: {e}")

    def _load_period_ranking_from_db(self, cache_key: tuple[str, int]) -> Optional[List[Dict]]:
        if not self._period_ranking_repository:
            return None
        target_date, days = cache_key
        try:
            return self._period_ranking_repository.get(target_date, days)
        except Exception as e:
            self._logger.warning(f"기간수급 DB 조회 실패: {e}")
            return None

    def _invalidate_intraday_period_cache(self) -> None:
        """장중에 수집된 기간수급 캐시를 무효화한다 (마감 후 완전본 재수집 유도)."""
        for key in self._period_ranking_intraday_keys:
            self._period_ranking_cache.pop(key, None)
            self._logger.info(f"장중 수집 기간수급 캐시 무효화: {key[0]}, {key[1]}일")
        self._period_ranking_intraday_keys.clear()

    async def _save_period_ranking_to_db(self, cache_key: tuple[str, int], results: List[Dict]) -> None:
        if not self._period_ranking_repository:
            return
        target_date, days = cache_key
        try:
            self._period_ranking_repository.save(target_date, days, results)
        except Exception as e:
            self._logger.warning(f"기간수급 DB 저장 실패: {e}")

    async def _get_or_collect_period_ranking(
        self,
        cache_key: tuple[str, int],
    ) -> List[Dict]:
        results = self._peek_period_ranking(cache_key)
        if results is not None:
            return results

        task = self._period_ranking_tasks.get(cache_key)
        if task is None:
            target_date, days = cache_key
            task = asyncio.create_task(
                self._collect_period_investor_program_ranking(target_date, days)
            )
            self._period_ranking_tasks[cache_key] = task
        try:
            results, is_complete = await task
        finally:
            if task.done() and self._period_ranking_tasks.get(cache_key) is task:
                self._period_ranking_tasks.pop(cache_key, None)

        if is_complete:
            self._period_ranking_cache[cache_key] = results
            if self._mcs and await self._mcs.is_market_open_now():
                # 장중 수집분은 당일 부분 데이터 — 장 마감 시 무효화·재수집 대상으로 표시
                self._period_ranking_intraday_keys.add(cache_key)
            else:
                await self._save_period_ranking_to_db(cache_key, results)
        return results

    async def _collect_period_investor_program_ranking(
        self,
        target_date: str,
        days: int,
    ) -> tuple[List[Dict], bool]:
        """기간 수급 원천 데이터를 수집한다. 불완전한 결과는 캐시하지 않는다."""
        all_stocks = self._load_all_stocks()
        if not all_stocks:
            return [], True

        recent_trading_dates = await self._get_recent_trading_dates(target_date, days)
        recent_trading_date_set = set(recent_trading_dates)
        industry_map = await self._load_industry_map()
        results: List[Dict] = []
        is_complete = True
        observed_trading_dates = {str(target_date)}

        for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
            await self._suspend_event.wait()

            investor_tasks = [
                self._fetch_with_retry(self._broker.get_investor_trade_by_stock_daily_multi, code, target_date, days)
                for code, _, _ in chunk
            ]
            program_tasks = [
                self._fetch_with_retry(self._broker.get_program_trade_by_stock_daily_multi, code, target_date, days)
                for code, _, _ in chunk
            ]
            all_responses = await asyncio.gather(
                *investor_tasks, *program_tasks, return_exceptions=True
            )
            investor_responses = all_responses[:len(chunk)]
            program_responses = all_responses[len(chunk):]

            for (code, name, _market), investor_resp, program_resp in zip(chunk, investor_responses, program_responses):
                if (
                    isinstance(investor_resp, Exception)
                    or isinstance(program_resp, Exception)
                    or not investor_resp
                    or not program_resp
                    or investor_resp.rt_cd != ErrorCode.SUCCESS.value
                    or program_resp.rt_cd != ErrorCode.SUCCESS.value
                ):
                    is_complete = False
                    continue

                investor_rows = investor_resp.data if investor_resp and isinstance(investor_resp.data, list) else []
                program_rows = program_resp.data if program_resp and isinstance(program_resp.data, list) else []
                if recent_trading_date_set:
                    investor_rows = [
                        row for row in investor_rows
                        if isinstance(row, dict) and str(row.get("stck_bsop_date") or "") in recent_trading_date_set
                    ]
                    program_rows = [
                        row for row in program_rows
                        if isinstance(row, dict) and str(row.get("stck_bsop_date") or "") in recent_trading_date_set
                    ]
                for row in [*investor_rows, *program_rows]:
                    if not isinstance(row, dict):
                        continue
                    trading_date = str(row.get("stck_bsop_date") or "")
                    if len(trading_date) == 8 and trading_date.isdigit():
                        observed_trading_dates.add(trading_date)

                frgn_qty = self._sum_rows(investor_rows, "frgn_ntby_qty")
                orgn_qty = self._sum_rows(investor_rows, "orgn_ntby_qty")
                frgn_pbmn_mil = self._sum_rows(investor_rows, "frgn_ntby_tr_pbmn")
                orgn_pbmn_mil = self._sum_rows(investor_rows, "orgn_ntby_tr_pbmn")
                program_qty = self._sum_rows(program_rows, "whol_smtn_ntby_qty")
                program_pbmn_won = self._sum_rows(program_rows, "whol_smtn_ntby_tr_pbmn")

                frgn_pbmn_won = frgn_pbmn_mil * 1_000_000
                orgn_pbmn_won = orgn_pbmn_mil * 1_000_000
                combined_pbmn_won = frgn_pbmn_won + orgn_pbmn_won + program_pbmn_won
                combined_qty = frgn_qty + orgn_qty + program_qty

                if combined_pbmn_won <= 0 and combined_qty <= 0:
                    continue

                first_investor = investor_rows[0] if investor_rows and isinstance(investor_rows[0], dict) else {}
                first_program = program_rows[0] if program_rows and isinstance(program_rows[0], dict) else {}
                stck_prpr = first_investor.get("stck_prpr") or first_program.get("stck_clpr") or "0"
                acml_tr_pbmn = first_investor.get("acml_tr_pbmn") or first_program.get("acml_tr_pbmn") or "0"

                results.append({
                    "stck_shrn_iscd": code,
                    "hts_kor_isnm": name,
                    "industry": industry_map.get(code, "-"),
                    "period_days": str(days),
                    "stck_prpr": str(stck_prpr or "0"),
                    "prdy_ctrt": str(first_investor.get("prdy_ctrt") or first_program.get("prdy_ctrt") or "0"),
                    "prdy_vrss": str(first_investor.get("prdy_vrss") or first_program.get("prdy_vrss") or "0"),
                    "prdy_vrss_sign": str(first_investor.get("prdy_vrss_sign") or first_program.get("prdy_vrss_sign") or ""),
                    "acml_tr_pbmn": str(acml_tr_pbmn or "0"),
                    "frgn_period_ntby_qty": str(frgn_qty),
                    "orgn_period_ntby_qty": str(orgn_qty),
                    "program_period_ntby_qty": str(program_qty),
                    "combined_period_ntby_qty": str(combined_qty),
                    "frgn_period_ntby_tr_pbmn": str(frgn_pbmn_mil),
                    "orgn_period_ntby_tr_pbmn": str(orgn_pbmn_mil),
                    "program_period_ntby_tr_pbmn": str(program_pbmn_won // 1_000_000),
                    "combined_period_ntby_tr_pbmn": str(combined_pbmn_won // 1_000_000),
                    "frgn_period_ntby_tr_pbmn_won": str(frgn_pbmn_won),
                    "orgn_period_ntby_tr_pbmn_won": str(orgn_pbmn_won),
                    "program_period_ntby_tr_pbmn_won": str(program_pbmn_won),
                    "combined_period_ntby_tr_pbmn_won": str(combined_pbmn_won),
                })

        earliest_trading_date = recent_trading_dates[0] if recent_trading_dates else min(observed_trading_dates)
        for result in results:
            result["earliest_trading_date"] = earliest_trading_date

        return results, is_complete

    async def _get_recent_trading_dates(self, target_date: str, days: int) -> List[str]:
        """시장 캘린더 기준 target_date 포함 최근 N거래일을 오래된 순으로 반환한다."""
        if self._mcs is None:
            return []

        current = datetime.strptime(str(target_date), "%Y%m%d")
        trading_dates: List[str] = []
        max_lookback_days = days * 3 + 15
        for _ in range(max_lookback_days):
            date_str = current.strftime("%Y%m%d")
            if await self._mcs.is_business_day(date_str):
                trading_dates.append(date_str)
                if len(trading_dates) == days:
                    return sorted(trading_dates)
            current -= timedelta(days=1)

        self._logger.warning(
            f"{target_date} 기준 최근 {days}거래일 계산 실패 — API 응답 날짜로 대체합니다."
        )
        return []

    async def get_trading_value_ranking(self, limit: int = 30) -> ResCommonResponse:
        """투자자 데이터 기반 거래대금 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._trading_value_cache, "거래대금", limit)

    async def _check_and_trigger_refresh(self) -> Optional[ResCommonResponse]:
        """캐시 비어있으면 온디맨드 갱신 트리거. 즉시 반환할 응답이 있으면 반환."""
        # [성능 보호] 장 중에는 온디맨드 갱신 트리거 안 함
        if self._mcs and await self._mcs.is_market_open_now():
            return None

        # 캐시 비어있고 갱신 중이 아니면 온디맨드 트리거
        if not self._foreign_net_buy_cache and not self._is_refreshing:
            try:
                asyncio.get_running_loop()
                self._logger.info("투자자 랭킹 캐시 없음 → 온디맨드 백그라운드 갱신 트리거")
                asyncio.create_task(self.refresh_investor_ranking())
            except RuntimeError:
                self._logger.warning("이벤트 루프 없음 — 온디맨드 갱신 스킵")
        return None

    # ── 외국인 ──

    async def get_foreign_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """외국인 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._foreign_net_buy_cache, "외국인 순매수", limit)

    async def get_foreign_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """외국인 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._foreign_net_sell_cache, "외국인 순매도", limit)

    # ── 기관 ──

    async def get_inst_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """기관 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._inst_net_buy_cache, "기관 순매수", limit)

    async def get_inst_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """기관 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._inst_net_sell_cache, "기관 순매도", limit)

    # ── 개인 ──

    async def get_prsn_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """개인 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._prsn_net_buy_cache, "개인 순매수", limit)

    async def get_prsn_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """개인 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._prsn_net_sell_cache, "개인 순매도", limit)

    # ── 프로그램 ──

    async def get_program_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """프로그램 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._program_net_buy_cache, "프로그램 순매수", limit)

    async def get_program_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """프로그램 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return await self._get_ranking_from_cache(self._program_net_sell_cache, "프로그램 순매도", limit)

    # ── 내부 헬퍼 ─────────────────────────────────────────────

    async def _get_ranking_from_cache(self, cache: List[Dict], label: str, limit: int) -> ResCommonResponse:
        """캐시에서 랭킹 데이터 반환. 캐시 없으면 트리거 + 빈 응답."""
        blocked = await self._check_and_trigger_refresh()
        if blocked:
            return blocked
        if not cache:
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="데이터 수집 중...",
                data=[]
            )
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1=f"{label} 상위 종목 조회 성공",
            data=cache[:limit]
        )

    def _load_all_stocks(self) -> List[tuple]:
        """StockCodeRepository에서 KOSPI/KOSDAQ 전체 종목 로드."""
        # 성능: iterrows()는 행마다 Series를 생성해 느리다. 컬럼을 리스트로 한 번 추출해
        # zip 순회한다. row.get(col, "") 시맨틱(컬럼 부재 시 "") 보존.
        df = self.stock_code_repository.df
        n = len(df)
        codes = df["종목코드"].tolist() if "종목코드" in df.columns else [""] * n
        names = df["종목명"].tolist() if "종목명" in df.columns else [""] * n
        markets = df["시장구분"].tolist() if "시장구분" in df.columns else [""] * n

        all_stocks = []
        for code, name, market in zip(codes, names, markets):
            if not code:
                continue

            # ETF/ETN 사전 필터링으로 불필요한 API 호출 방지
            if any(name.startswith(p) for p in _ETF_PREFIXES):
                continue

            # [성능 개선] 우선주(코드 끝자리가 0이 아님) 및 스팩(SPAC) 제외
            if code[-1] != '0':
                continue
            if "스팩" in name:
                continue

            if market in ("KOSPI", "KOSDAQ"):
                all_stocks.append((code, name, market))
        return all_stocks

    async def force_run(self) -> None:
        """강제 수집: skip 조건을 무시하고 투자자 랭킹을 재수집한다."""
        self._logger.info("RankingTask 강제 수집 요청")
        async with self._running_state():
            await self.refresh_investor_ranking(force=True)
