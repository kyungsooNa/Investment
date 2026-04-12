# task/background/after_market/newhigh_task.py
"""
장 마감 후 52주 신고가 종목을 감지하여 텔레그램으로 전송하는 백그라운드 태스크.
daily_prices 스냅샷의 w52_high 기준으로 current_price >= w52_high 종목을 필터링한다.
"""
import asyncio
import logging
from typing import List, Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskState
from typing import Dict
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from services.telegram_notifier import TelegramReporter
    from repositories.stock_repository import StockRepository
    from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
    from services.stock_query_service import StockQueryService


# ETF/ETN 브랜드명 접두사 (RankingTask._ETF_PREFIXES 와 동일)
_ETF_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
    "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
    "VITA", "TREX", "MASTER", "WOORI", "KINDEX",
)


class NewHighTask(AfterMarketTask):
    """장 마감 후 52주 신고가 종목을 감지하여 텔레그램으로 전송하는 태스크."""

    def __init__(
        self,
        stock_repo: "StockRepository",
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        telegram_reporter: Optional["TelegramReporter"] = None,
        notification_service: Optional[NotificationService] = None,
        daily_price_collector_task: Optional["DailyPriceCollectorTask"] = None,
        stock_query_service: Optional["StockQueryService"] = None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
        )
        self._stock_repo = stock_repo
        self._telegram_reporter = telegram_reporter
        self._notification_service = notification_service
        self._daily_price_collector_task = daily_price_collector_task
        self._stock_query_service = stock_query_service
        self._last_collected_date: Optional[str] = None
        self._progress: Dict = {"running": False, "last_date": None, "newhigh_count": 0, "status": None}

    # ── SchedulableTask 인터페이스 구현 ────────────────────────────

    @property
    def task_name(self) -> str:
        return "newhigh"

    @property
    def _scheduler_label(self) -> str:
        return "NewHighTask"

    def get_progress(self) -> Dict:
        return dict(self._progress)

    async def start(self) -> None:
        """장마감 후 자동 스케줄러 시작."""
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._tasks.append(asyncio.create_task(self._after_market_scheduler()))
        self._logger.info("NewHighTask 시작")

    # ── 장마감 후 콜백 ──────────────────────────────────────────────

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """당일 daily_prices 스냅샷에서 신고가 종목을 감지하고 텔레그램으로 전송한다."""
        if self._last_collected_date == latest_trading_date:
            self._logger.info(f"NewHighTask: {latest_trading_date} 이미 처리됨, 건너뜀")
            return
        await self._run_newhigh(latest_trading_date)

    async def _run_newhigh(self, latest_trading_date: str) -> None:
        self._progress["running"] = True
        try:
            self._logger.info(f"NewHighTask: {latest_trading_date} 신고가 탐색 시작")
            snapshots = await self._stock_repo.get_all_daily_snapshots(latest_trading_date)

            if not snapshots:
                self._logger.warning(f"NewHighTask: daily_prices 데이터 없음 (date={latest_trading_date})")
                return

            # w52_high 데이터 부재 시 DailyPriceCollectorTask로 강제 최신화 후 재조회
            if self._daily_price_collector_task and not self._has_sufficient_w52_data(snapshots):
                self._logger.warning(
                    f"NewHighTask: w52_high 데이터 부재 — DailyPriceCollectorTask.force_collect() 실행 후 재조회"
                )
                self._progress["status"] = "DailyPriceCollector 데이터 수집 중..."
                await self._daily_price_collector_task.force_collect()
                self._progress["status"] = None
                snapshots = await self._stock_repo.get_all_daily_snapshots(latest_trading_date)

            newhigh_stocks = self._filter_newhigh(snapshots)
            
            # 600일치 데이터를 바탕으로 역사적 신고가 판별
            if self._stock_query_service and newhigh_stocks:
                newhigh_stocks = await self._enrich_historical_high(newhigh_stocks)
                
            self._logger.info(
                f"NewHighTask: 신고가 {len(newhigh_stocks)}개 감지 / 전체 {len(snapshots)}개 (date={latest_trading_date})"
            )
    
            if self._telegram_reporter:
                await self._telegram_reporter.send_newhigh_report(newhigh_stocks, latest_trading_date)
    
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.INFO,
                    "52주 신고가 리포트",
                    f"{len(newhigh_stocks)}개 종목 감지 (date={latest_trading_date})",
                )
    
            self._last_collected_date = latest_trading_date
            self._progress.update({"last_date": latest_trading_date, "newhigh_count": len(newhigh_stocks)})
        except Exception as e:
            self._logger.error(f"NewHighTask 신고가 탐색 중 오류 발생: {e}", exc_info=True)
        finally:
            self._progress["running"] = False
            self._progress["status"] = None

    async def force_collect(self) -> None:
        """skip 조건을 무시하고 즉시 52주 신고가 탐색을 실행한다."""
        self._logger.info("NewHighTask 강제 실행 요청")
        async with self._running_state():
            target_date = None
            if self._mcs:
                target_date = await self._mcs.get_latest_trading_date()
            if not target_date:
                self._logger.error("최근 거래일을 확인할 수 없어 강제 실행을 중단합니다.")
                return
            await self._run_newhigh(target_date)

    # ── 내부 헬퍼 ──────────────────────────────────────────────────

    @staticmethod
    def _has_sufficient_w52_data(snapshots: List[Dict]) -> bool:
        """스냅샷 중 20% 이상에 유효한 w52_high 값이 있으면 True (FDR 수집 시 전량 None 케이스 감지용)."""
        if not snapshots:
            return True
        valid = sum(1 for s in snapshots if (s.get("w52_high") or 0) > 0)
        return valid / len(snapshots) >= 0.2

    def _filter_newhigh(self, snapshots: List[Dict]) -> List[Dict]:
        """current_price >= w52_high 인 종목 반환 (ETF/ETN 제외, 시가총액 내림차순)."""
        result = []
        for s in snapshots:
            name = s.get("name") or ""
            if name == "RF머트리얼즈":
                a = 1
            if any(name.startswith(p) for p in _ETF_PREFIXES):
                continue
            current = s.get("current_price") or 0
            high = s.get("high_price") or 0
            w52 = s.get("w52_high") or 0
            volume = s.get("volume") or 0
            trading_value = s.get("trading_value") or 0 # 거래대금

            # 1. 거래량이 0이거나 거래대금이 너무 적은 종목 제외
            # (예: 당일 거래대금 최소 1억 이상인 종목만 주도주 후보로 인정)
            if volume <= 0 or trading_value < 100_000_000:
                continue
            # 2. 일단 기술적 신고가 여부 확인
            if current > 0 and w52 > 0 and high >= w52:
                # 3. 전략적 필터: 고가 대비 종가가 너무 밀렸는가? (유지율 97% 이상 권장)
                # 71,700원 고가 대비 68,100원 종가라면 유지율이 95% 미만이므로 탈락
                maintenance_ratio = current / high
                if maintenance_ratio >= 0.97: 
                    # 4. 추가 조건: 거래량 폭발 등 (선택 사항)
                    # 추후 RS(Relative Strength) 등 추가 데이터를 위한 자리표시자
                    s.setdefault("rs", "-")
                    result.append(s)

        result.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
        return result

    async def _enrich_historical_high(self, stocks: List[Dict]) -> List[Dict]:
        """OHLCV 600일치를 조회하여 역사적 신고가(600일 내 최고가) 여부를 판별합니다."""
        enriched = []
        for s in stocks:
            code = s.get("code")
            s["is_historical_new_high"] = False  # 기본값
            
            if not code:
                enriched.append(s)
                continue
            
            try:
                resp = await self._stock_query_service.get_ohlcv(code, period="D", caller="NewHighTask")
                if resp and str(resp.rt_cd) == "0" and resp.data:
                    ohlcv = resp.data
                    if ohlcv:
                        max_high = max((float(candle.get("high", 0)) for candle in ohlcv), default=0.0)
                        current_high = float(s.get("high_price") or s.get("current_price") or 0)
                        
                        if current_high >= max_high and current_high > 0:
                            s["is_historical_new_high"] = True
            except Exception as e:
                self._logger.warning(f"NewHighTask: {code} 역사적 신고가 판별 위한 OHLCV 조회 실패 - {e}")
            
            enriched.append(s)
        return enriched
