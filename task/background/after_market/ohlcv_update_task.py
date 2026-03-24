# task/background/ohlcv_update_task.py
"""
장 마감 후 전체 종목의 OHLCV를 DB에 저장하는 백그라운드 태스크.
- 당일 OHLCV 및 전략에 필요한 최대 600일치 역사 데이터를 유지한다.
- DB에 이미 존재하는 날짜는 API를 호출하지 않아 불필요한 중복 요청을 방지한다.
"""
import asyncio
import logging
import time
from typing import List, Dict, Optional, TYPE_CHECKING

from common.types import ErrorCode
from core.performance_profiler import PerformanceProfiler
from core.market_clock import MarketClock
from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskState
from repositories.stock_repository import StockRepository
from repositories.stock_code_repository import StockCodeRepository
from services.market_calendar_service import MarketCalendarService
from services.notification_service import NotificationService, NotificationCategory

if TYPE_CHECKING:
    from services.stock_query_service import StockQueryService


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


# ETF/ETN 브랜드명 접두사 (DailyPriceCollectorTask와 동일)
_ETF_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
    "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
    "VITA", "TREX", "MASTER", "WOORI", "KINDEX",
)


class OhlcvUpdateTask(AfterMarketTask):
    """장 마감 후 전체 종목의 OHLCV를 수집하여 DB에 저장하는 백그라운드 태스크.

    - DB에 이미 TARGET_OHLCV_DAYS일치 데이터가 있고 당일 날짜까지 갱신된 종목은 스킵.
    - 데이터가 부족하거나 당일 캔들이 없는 종목만 API를 호출하여 저장.
    - StockQueryService.get_ohlcv()가 내부적으로 누락 구간만 API 호출 후 DB에 upsert하므로
      중복된 날짜는 자동으로 INSERT OR REPLACE 처리된다.
    """

    TARGET_OHLCV_DAYS = 600  # 전략에서 최대 600일치를 사용하므로 동일하게 유지
    API_CHUNK_SIZE = 4        # 병렬 처리 종목 수 (OHLCV는 현재가보다 API 비용이 높음)
    CHUNK_SLEEP_SEC = 1.5     # 청크 간 대기 시간 (API 레이트 리밋 준수)

    def __init__(
        self,
        stock_query_service: "StockQueryService",
        stock_code_repository: StockCodeRepository,
        stock_repo: StockRepository,
        market_calendar_service: Optional[MarketCalendarService] = None,
        market_clock: Optional[MarketClock] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        logger=None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
        )
        self._stock_query_service = stock_query_service
        self.stock_code_repository = stock_code_repository
        self._stock_repo = stock_repo
        self._pm = performance_profiler or PerformanceProfiler(enabled=False)
        self._ns = notification_service
        self._suspend_event: asyncio.Event = asyncio.Event()
        self._suspend_event.set()  # 초기에는 실행 가능

        # 수집 상태
        self._is_collecting: bool = False
        self._last_collected_date: Optional[str] = None
        self._progress: Dict = {
            "running": False,
            "processed": 0,
            "total": 0,
            "updated": 0,
            "skipped": 0,
            "elapsed": 0.0,
        }

    # ── SchedulableTask 인터페이스 구현 ────────────────────────

    @property
    def task_name(self) -> str:
        return "ohlcv_update"

    @property
    def _scheduler_label(self) -> str:
        return "OhlcvUpdate"

    async def start(self) -> None:
        """수집 1회 실행 + 장마감 후 자동 스케줄러 시작."""
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._suspend_event.set()

        self._tasks.append(
            asyncio.create_task(self._collect_all_ohlcv())
        )
        self._tasks.append(
            asyncio.create_task(self._after_market_scheduler())
        )
        self._logger.info(f"OhlcvUpdateTask 시작: {len(self._tasks)}개 태스크")

    async def suspend(self) -> None:
        """수집을 일시 중지한다 (chunk 사이에서 대기)."""
        if self._state == TaskState.RUNNING:
            self._suspend_event.clear()
            self._state = TaskState.SUSPENDED
            self._logger.info("OhlcvUpdateTask 일시 중지")

    async def resume(self) -> None:
        """일시 중지된 수집을 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._suspend_event.set()
            self._state = TaskState.RUNNING
            self._logger.info("OhlcvUpdateTask 재개")

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백: 해당 거래일의 수집이 필요하면 실행."""
        if self._last_collected_date != latest_trading_date:
            await self._collect_all_ohlcv()

    async def force_collect(self) -> None:
        """강제 전체 수집: skip 조건을 무시하고 모든 종목을 API 재호출한다.

        - 최초 설치(로컬 DB 없음) 또는 다른 머신으로 이전 시 전체 백필 보장
        - 중간 날짜 누락 등 데이터 정합성이 의심될 때 사용
        """
        self._logger.info("OhlcvUpdateTask 강제 수집 요청")
        await self._collect_all_ohlcv(force=True)

    # ── 전체 종목 OHLCV 수집 ────────────────────────────────

    async def _collect_all_ohlcv(self, force: bool = False) -> None:
        """전체 종목 OHLCV를 수집하여 DB에 저장한다.

        Args:
            force: True이면 skip 조건(count/latest_date)을 무시하고 전 종목 API 재호출.
        """
        if not force and self._mcs and await self._mcs.is_market_open_now():
            self._logger.info("장 운영 중이므로 OHLCV 수집을 건너뜁니다.")
            return

        if self._is_collecting:
            self._logger.info("OHLCV 수집 이미 진행 중 — 스킵")
            return

        t_start_total = self._pm.start_timer()
        self._is_collecting = True
        start_time = time.time()

        try:
            # 기준일 확인
            target_date = None
            if self._mcs:
                target_date = await self._mcs.get_latest_trading_date()

            if not target_date:
                self._logger.error("최근 거래일을 확인할 수 없어 OHLCV 수집을 중단합니다.")
                return

            if not force and self._last_collected_date == target_date:
                self._logger.info(f"이미 {target_date} OHLCV 수집 완료 — 스킵")
                return

            self._logger.info(f"전체 종목 OHLCV 수집 시작 (기준일: {target_date})")
            self._progress = {
                "running": True, "force": force, "processed": 0, "total": 0,
                "updated": 0, "skipped": 0, "elapsed": 0.0,
            }
            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            self._progress["total"] = total
            self._logger.info(f"OHLCV 수집: 전체 {total}개 종목 순회 시작")

            processed = 0
            updated = 0
            skipped = 0

            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                # suspend 체크포인트
                await self._suspend_event.wait()

                # 병렬 처리
                tasks = [
                    self._update_stock_ohlcv(code, target_date, force=force)
                    for code, _, _ in chunk
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                chunk_had_api_call = False
                for result in results:
                    if result is True:
                        updated += 1
                        chunk_had_api_call = True
                    elif result is False:
                        skipped += 1
                    # None은 오류 — 카운트 제외

                processed += len(chunk)
                elapsed = time.time() - start_time
                self._progress.update({
                    "processed": processed,
                    "updated": updated,
                    "skipped": skipped,
                    "elapsed": round(elapsed, 1),
                })

                if processed % 100 == 0 or processed >= total:
                    self._logger.info(
                        f"OHLCV 수집 진행: {processed}/{total} "
                        f"({processed / total * 100:.1f}%) "
                        f"| 갱신: {updated} | 스킵: {skipped} | 소요: {elapsed:.1f}s"
                    )

                # API 호출이 있었던 청크만 rate limit 대기
                if chunk_had_api_call:
                    await asyncio.sleep(self.CHUNK_SLEEP_SEC)
                else:
                    await asyncio.sleep(0)  # 이벤트 루프 양보만

            # 완료 처리
            self._last_collected_date = target_date
            elapsed = time.time() - start_time
            self._logger.info(
                f"전체 종목 OHLCV 수집 완료: 갱신 {updated}개 / 스킵 {skipped}개, "
                f"소요: {elapsed:.1f}s"
            )
            self._pm.log_timer(
                "OhlcvUpdateTask._collect_all_ohlcv",
                t_start_total, threshold=10.0,
            )
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.BACKGROUND, "info", "전체 종목 OHLCV 수집 완료",
                    f"갱신 {updated}개, 소요: {elapsed:.1f}초",
                )

        except Exception as e:
            self._logger.error(f"OHLCV 수집 실패: {e}", exc_info=True)
            if self._ns:
                await self._ns.emit(NotificationCategory.BACKGROUND, "error", "OHLCV 수집 실패", str(e))
        finally:
            self._is_collecting = False
            self._progress["running"] = False

    # ── 내부 헬퍼 ─────────────────────────────────────────

    async def _update_stock_ohlcv(
        self, code: str, target_date: str, force: bool = False,
    ) -> Optional[bool]:
        """단일 종목 OHLCV를 필요 시에만 API 호출하여 업데이트한다.

        DB 상태를 먼저 조회하여:
        - 당일 데이터가 이미 존재하면 → 스킵 (False)
          (역사 데이터는 최초 실행 시 full backfill로 채워지며, 이후에는 force collect로 복구)
        - 당일 데이터 없으면 → get_ohlcv() 호출 후 DB 저장 (True)

        Args:
            force: True이면 skip 조건을 무시하고 무조건 API 호출.

        Returns:
            True  - API 호출 후 갱신 성공
            False - 이미 최신 상태여서 스킵
            None  - 오류 발생
        """
        try:
            if not force:
                summary = self._stock_repo.get_ohlcv_summary(code)
                latest_date = summary["latest_date"]

                # 당일 캔들이 이미 존재하면 API 불필요
                if latest_date == target_date:
                    return False

            # get_ohlcv: DB에 없는 구간은 자동으로 API 조회 후 StockRepository에 upsert
            resp = await self._stock_query_service.get_ohlcv(code, caller="OhlcvUpdateTask")
            if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                return True
            return None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.warning(f"OHLCV 업데이트 실패 ({code}): {e}")
            return None

    def _load_all_stocks(self) -> List[tuple]:
        """StockCodeRepository에서 KOSPI/KOSDAQ 전체 종목 로드 (ETF/우선주 제외)."""
        all_stocks = []
        for _, row in self.stock_code_repository.df.iterrows():
            code = row.get("종목코드", "")
            name = row.get("종목명", "")
            market = row.get("시장구분", "")

            if not code:
                continue
            if any(name.startswith(p) for p in _ETF_PREFIXES):
                continue
            if code[-1] != '0':
                continue
            if "스팩" in name:
                continue
            if market in ("KOSPI", "KOSDAQ"):
                all_stocks.append((code, name, market))
        return all_stocks

    def get_progress(self) -> Dict:
        """수집 진행률 반환."""
        return dict(self._progress)
