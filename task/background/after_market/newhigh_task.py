# task/background/after_market/newhigh_task.py
"""
장 마감 후 52주 신고가 종목을 감지하여 텔레그램으로 전송하는 백그라운드 태스크.
daily_prices 스냅샷의 w52_high 기준으로 current_price >= w52_high 종목을 필터링한다.
"""
import asyncio
import logging
import time
from typing import List, Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask
from typing import Dict
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from services.telegram_notifier import TelegramReporter
    from repositories.stock_repository import StockRepository
    from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
    from services.stock_query_service import StockQueryService
    from services.rs_rating_service import RSRatingService


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
        rs_rating_service: Optional["RSRatingService"] = None,
        rs_rating_min: int = 80,
        worker_pool=None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._stock_repo = stock_repo
        self._telegram_reporter = telegram_reporter
        self._notification_service = notification_service
        self._daily_price_collector_task = daily_price_collector_task
        self._stock_query_service = stock_query_service
        self._rs_rating_service = rs_rating_service
        self._rs_rating_min = rs_rating_min  # 0이면 RS Rating 필터 비활성
        self._newhigh_cache: List[Dict] = []
        self._last_collected_date: Optional[str] = None
        self._run_lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None
        self._progress: Dict = {
            "running": False,
            "last_date": None,
            "newhigh_count": 0,
            "status": None,
            "processed": 0,
            "total": 0,
            "collected": 0,
            "elapsed": 0.0,
            "last_error": None,
        }

    # ── SchedulableTask 인터페이스 구현 ────────────────────────────

    @property
    def task_name(self) -> str:
        return "newhigh"

    @property
    def _scheduler_label(self) -> str:
        return "NewHighTask"

    def get_progress(self) -> Dict:
        return dict(self._progress)

    async def get_newhigh_cache(self, limit: int = 200):
        """현재 메모리에 캐시된 신고가 종목 목록을 반환한다 (웹 UI 등에서 호출)."""
        if not self._newhigh_cache and not self._progress.get("running"):
            self.trigger_refresh()
        return self._newhigh_cache[:limit]

    def trigger_refresh(self) -> bool:
        """캐시가 비었을 때 신고가 갱신을 한 번만 백그라운드로 예약한다."""
        if self._refresh_task and not self._refresh_task.done():
            return False

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._logger.warning("이벤트 루프 없음 — NewHigh 즉시 갱신 스킵")
            return False

        self._progress["running"] = True
        self._progress["status"] = "신고가 갱신 대기 중..."
        self._refresh_task = asyncio.create_task(self.force_run())
        self._refresh_task.add_done_callback(self._clear_refresh_task)
        return True

    def _clear_refresh_task(self, task: asyncio.Task) -> None:
        if self._refresh_task is task:
            self._refresh_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(f"NewHighTask 백그라운드 갱신 오류: {e}", exc_info=True)
        if self._progress.get("status") == "신고가 갱신 대기 중...":
            self._progress["status"] = None
        if self._progress.get("running") and not self._run_lock.locked():
            self._progress["running"] = False

    # ── 장마감 후 콜백 ──────────────────────────────────────────────

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """당일 daily_prices 스냅샷에서 신고가 종목을 감지하고 텔레그램으로 전송한다."""
        if self._last_collected_date == latest_trading_date:
            self._logger.info(f"NewHighTask: {latest_trading_date} 이미 처리됨, 건너뜀")
            return
        await self._run_newhigh(latest_trading_date)

    async def _run_newhigh(self, latest_trading_date: str) -> None:
        async with self._run_lock:
            await self._run_newhigh_locked(latest_trading_date)

    async def _run_newhigh_locked(self, latest_trading_date: str) -> None:
        if self._last_collected_date == latest_trading_date:
            self._logger.info(f"NewHighTask: {latest_trading_date} 이미 처리됨, 건너뜀")
            return

        self._progress.update({
            "running": True,
            "status": "신고가 탐색 중...",
            "processed": 0,
            "total": 0,
            "collected": 0,
            "elapsed": 0.0,
            "last_error": None,
        })
        start_time = time.time()
        try:
            snapshots = await self._load_snapshots_for_newhigh(latest_trading_date)
            if not snapshots:
                return

            newhigh_stocks = self._filter_newhigh(snapshots)
            if self._stock_query_service and newhigh_stocks:
                newhigh_stocks = await self._enrich_historical_high(newhigh_stocks)
            if self._rs_rating_service and newhigh_stocks:
                newhigh_stocks = await self._enrich_and_filter_rs_rating(
                    newhigh_stocks, latest_trading_date
                )

            self._newhigh_cache = newhigh_stocks
            await self._write_newhigh_fields(latest_trading_date, newhigh_stocks)

            elapsed = time.time() - start_time
            self._logger.info(
                f"NewHighTask: 신고가 {len(newhigh_stocks)}개 감지 / 전체 {len(snapshots)}개 "
                f"(date={latest_trading_date}) / 소요: {elapsed:.1f}s"
            )

            self._last_collected_date = latest_trading_date
            self._progress.update({
                "last_date": latest_trading_date,
                "newhigh_count": len(newhigh_stocks),
                "collected": len(newhigh_stocks),
                "elapsed": elapsed,
            })
            await self._send_reports(newhigh_stocks, latest_trading_date, elapsed)
        except Exception as e:
            self._progress["last_error"] = str(e)
            self._logger.error(f"NewHighTask 신고가 탐색 중 오류 발생: {e}", exc_info=True)
        finally:
            self._progress["running"] = False
            self._progress["status"] = None

    async def _load_snapshots_for_newhigh(self, latest_trading_date: str) -> List[Dict]:
        self._logger.info(f"NewHighTask: {latest_trading_date} 신고가 탐색 시작")
        snapshots = await self._stock_repo.get_all_daily_snapshots(latest_trading_date)
        if not snapshots:
            message = f"daily_prices 데이터 없음 (date={latest_trading_date})"
            self._logger.warning(f"NewHighTask: {message}")
            self._progress["last_error"] = message
            return []

        self._progress.update({"total": len(snapshots), "processed": len(snapshots)})
        if self._daily_price_collector_task and not self._has_sufficient_w52_data(snapshots):
            self._logger.warning(
                "NewHighTask: w52_high 데이터 부재 — DailyPriceCollectorTask.force_run() 실행 후 재조회"
            )
            self._progress["status"] = "DailyPriceCollector 데이터 수집 중..."
            await self._daily_price_collector_task.force_run()
            self._progress["status"] = "신고가 탐색 중..."
            snapshots = await self._stock_repo.get_all_daily_snapshots(latest_trading_date)
            self._progress.update({"total": len(snapshots), "processed": len(snapshots)})

        if not snapshots:
            message = f"DailyPriceCollector 재수집 후에도 daily_prices 데이터 없음 (date={latest_trading_date})"
            self._logger.warning(f"NewHighTask: {message}")
            self._progress["last_error"] = message
            return []
        return snapshots

    async def _write_newhigh_fields(self, trade_date: str, stocks: List[Dict]) -> None:
        try:
            records = [
                {
                    "code": s.get("code"),
                    "is_newhigh": True,
                    "is_historical_new_high": s.get("is_historical_new_high", False),
                }
                for s in stocks
                if s.get("code")
            ]
            await self._stock_repo.update_newhigh_fields(trade_date, records)
        except Exception as e:
            self._logger.warning(f"NewHighTask DB에 쓰기 실패: {e}")

    async def _send_reports(self, stocks: List[Dict], latest_trading_date: str, elapsed: float) -> None:
        if self._telegram_reporter:
            try:
                await self._telegram_reporter.send_newhigh_report(stocks, latest_trading_date)
            except Exception as e:
                self._logger.warning(f"NewHighTask 텔레그램 리포트 전송 실패: {e}")

        if self._notification_service:
            try:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.INFO,
                    "52주 신고가 리포트",
                    f"{len(stocks)}개 종목 감지 (date={latest_trading_date}) / 소요: {elapsed:.1f}초",
                )
            except Exception as e:
                self._logger.warning(f"NewHighTask 알림 발행 실패: {e}")

    async def force_run(self) -> None:
        """skip 조건을 무시하고 즉시 52주 신고가 탐색을 실행한다."""
        self._logger.info("NewHighTask 강제 실행 요청")
        async with self._running_state():
            target_date = None
            if self._mcs:
                target_date = await self._mcs.get_latest_trading_date()
            if not target_date:
                self._logger.error("최근 거래일을 확인할 수 없어 강제 실행을 중단합니다.")
                self._progress.update({"running": False, "status": None, "last_error": "최근 거래일 확인 실패"})
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

    @staticmethod
    def _normalize_snapshot_for_newhigh(snapshot: Dict) -> Dict:
        """신고가 판정 전 OHLC/전일대비 모순을 보정한다."""
        current = int(snapshot.get("current_price") or 0)
        open_price = int(snapshot.get("open_price") or 0)
        high = int(snapshot.get("high_price") or 0)
        low = int(snapshot.get("low_price") or 0)
        prev_close = int(snapshot.get("prev_close") or 0)
        change_price = int(snapshot.get("change_price") or 0)

        if current > 0:
            candidates = [p for p in (open_price, high, low, current) if p > 0]
            if candidates:
                snapshot["high_price"] = max(candidates)
                snapshot["low_price"] = min(candidates)

        expected_change = current - prev_close if current > 0 and prev_close > 0 else change_price
        if current > 0 and prev_close > 0 and expected_change != change_price:
            snapshot["change_price"] = expected_change
            if expected_change > 0:
                snapshot["change_sign"] = "2"
            elif expected_change < 0:
                snapshot["change_sign"] = "5"
            else:
                snapshot["change_sign"] = "3"
            snapshot["change_rate"] = str(round(expected_change / prev_close * 100, 2))

        return snapshot

    def _filter_newhigh(self, snapshots: List[Dict]) -> List[Dict]:
        """current_price >= w52_high 인 종목 반환 (ETF/ETN 제외, 시가총액 내림차순)."""
        result = []
        for s in snapshots:
            s = self._normalize_snapshot_for_newhigh(s)
            name = s.get("name") or ""
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
                    # rs_rating(DB 컬럼) 값을 rs 필드로 전달; 없으면 "-" 기본값
                    s["rs"] = s.get("rs") or s.get("rs_rating") or "-"
                    s["is_newhigh"] = True
                    result.append(s)

        result.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
        return result

    async def _enrich_and_filter_rs_rating(
        self, stocks: List[Dict], trade_date: str
    ) -> List[Dict]:
        """RS Rating을 각 종목에 주입하고 rs_rating_min 미만 종목을 제거합니다.

        DB에 당일 데이터가 없으면 필터링을 건너뛰고 rs_rating=0으로 주입합니다.
        """
        try:
            resp = await self._rs_rating_service.get_ratings_by_date(trade_date)
            if resp.rt_cd != "0" or not resp.data:
                # 데이터 없으면 rs_rating=0 주입 후 필터 미적용
                for s in stocks:
                    s.setdefault("rs_rating", 0)
                return stocks

            rating_map: Dict[str, int] = resp.data
        except Exception as e:
            self._logger.warning(f"NewHighTask: RS Rating 조회 실패 ({e}) — 필터 건너뜀")
            for s in stocks:
                s.setdefault("rs_rating", 0)
            return stocks

        matched_count = sum(1 for s in stocks if s.get("code", "") in rating_map)
        coverage = matched_count / len(stocks) if stocks else 1.0
        if self._rs_rating_min > 0 and coverage < 0.2:
            self._logger.warning(
                f"NewHighTask: RS Rating 매칭률 낮음 ({matched_count}/{len(stocks)}) — 필터 건너뜀"
            )
            for s in stocks:
                s.setdefault("rs_rating", 0)
            return stocks

        result = []
        for s in stocks:
            code = s.get("code", "")
            rating = rating_map.get(code, 0)
            s["rs_rating"] = rating
            if self._rs_rating_min > 0 and rating < self._rs_rating_min:
                continue
            result.append(s)

        filtered_out = len(stocks) - len(result)
        if filtered_out > 0:
            self._logger.info(
                f"NewHighTask: RS Rating < {self._rs_rating_min} 종목 {filtered_out}개 제외"
            )
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
