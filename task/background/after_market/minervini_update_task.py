"""
Minervini Stage2 결과를 주기적으로 수집하여 캐시하는 백그라운드 태스크.
RankngTask를 참고하여 단순화한 구현입니다.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional

from core.market_clock import MarketClock
from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskState
from repositories.stock_code_repository import StockCodeRepository
from core.performance_profiler import PerformanceProfiler
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


class MinerviniUpdateTask(AfterMarketTask):
    """Minervini Stage2 종목을 백그라운드에서 수집하여 캐시한다."""

    API_CHUNK_SIZE = 12
    CHUNK_SLEEP_SEC = 1.0

    def __init__(
        self,
        minervini_service,
        stock_code_repository: StockCodeRepository,
        stock_query_service=None,
        broker_api_wrapper=None,
        rs_rating_service=None,
        market_clock: MarketClock = None,
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        market_calendar_service=None,
    ):
        super().__init__(mcs=market_calendar_service, market_clock=market_clock, logger=logger or logging.getLogger(__name__))
        self._minervini = minervini_service
        self.stock_code_repository = stock_code_repository
        self._sqs = stock_query_service
        self._broker = broker_api_wrapper
        self._rs_svc = rs_rating_service
        self.pm = performance_profiler or PerformanceProfiler(enabled=False)
        self._notification_service = notification_service
        self._suspend_event: asyncio.Event = asyncio.Event()
        self._suspend_event.set()

        self._minervini_stage2_cache: List[Dict] = []
        self._updated_at: Optional[datetime] = None
        self._is_refreshing = False

    @property
    def task_name(self) -> str:
        return "minervini_update"

    @property
    def _scheduler_label(self) -> str:
        return "MinerviniUpdateTask"

    async def start(self) -> None:
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._suspend_event.set()
        self._tasks.append(asyncio.create_task(self.start_after_market_scheduler()))
        self._logger.info("MinerviniUpdateTask 시작")

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._suspend_event.clear()
            self._state = TaskState.SUSPENDED
            self._logger.info("MinerviniUpdateTask 일시 중지")

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._suspend_event.set()
            self._state = TaskState.RUNNING
            self._logger.info("MinerviniUpdateTask 재개")

    async def start_after_market_scheduler(self) -> None:
        await self._after_market_scheduler()

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        needs = (not self._updated_at) or (self._updated_at and self._updated_at.strftime('%Y-%m-%d') != latest_trading_date)
        if needs:
            await self.refresh_minervini_stage2()

    def _load_all_stocks(self) -> List[tuple]:
        all_stocks = []
        for _, row in self.stock_code_repository.df.iterrows():
            code = row.get("종목코드", "")
            name = row.get("종목명", "")
            market = row.get("시장구분", "")
            if not code:
                continue
            all_stocks.append((code, name, market))
        return all_stocks

    async def refresh_minervini_stage2(self, force: bool = False) -> None:
        """전체 종목을 순회하여 Minervini Stage2 종목을 수집하여 캐시에 저장한다."""
        if self._mcs and await self._mcs.is_market_open_now():
            self._logger.info("장 중이므로 Minervini Stage2 백그라운드 갱신을 건너뜁니다.")
            return

        if self._is_refreshing:
            self._logger.info("Minervini 갱신 이미 진행 중 — 스킵")
            return

        self._is_refreshing = True
        start_time = time.time()
        self._logger.info("Minervini Stage2 백그라운드 갱신 시작")

        try:
            target_date = None
            if self._mcs:
                target_date = await self._mcs.get_latest_trading_date()

            if not force and self._updated_at and target_date and self._updated_at.strftime('%Y-%m-%d') == target_date:
                self._logger.info(f"이미 {target_date} Minervini Stage2 갱신 완료 — 스킵")
                self._is_refreshing = False
                return

            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            collected: List[Dict] = []

            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                await self._suspend_event.wait()

                # 1) 우선 Stage 판정만 호출
                tasks = [self._minervini.get_stage_for_code(code) for code, _, _ in chunk]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                # 2) Stage2인 종목만 후속 정보 수집
                stage2_codes = []
                code_map = {}
                for (code, name, market), resp in zip(chunk, responses):
                    if isinstance(resp, Exception):
                        continue
                    try:
                        stage = resp[0] if isinstance(resp, (list, tuple)) else resp
                    except Exception:
                        continue
                    if stage == 2:
                        stage2_codes.append(code)
                        code_map[code] = {"code": code, "name": name, "stage": 2}

                # 3) Stage2 종목의 현재가/시가총액/RS 병렬 수집
                follow_tasks = []
                for code in stage2_codes:
                    # get_current_price via stock_query_service (if available)
                    if self._sqs:
                        follow_tasks.append(self._sqs.get_current_price(code, caller="MinerviniUpdateTask"))
                    else:
                        follow_tasks.append(asyncio.sleep(0, result=None))

                follow_resps = await asyncio.gather(*follow_tasks, return_exceptions=True)

                # RS & market cap collection (best-effort)
                mcap_tasks = []
                rs_tasks = []
                for code in stage2_codes:
                    if self._broker:
                        mcap_tasks.append(self._broker.get_market_cap(code))
                    else:
                        mcap_tasks.append(asyncio.sleep(0, result=None))
                    if self._rs_svc:
                        rs_tasks.append(self._rs_svc.get_rating(code))
                    else:
                        rs_tasks.append(asyncio.sleep(0, result=None))

                mcap_resps = await asyncio.gather(*mcap_tasks, return_exceptions=True)
                rs_resps = await asyncio.gather(*rs_tasks, return_exceptions=True)

                # assemble
                for code, price_resp, mcap_resp, rs_resp in zip(stage2_codes, follow_resps, mcap_resps, rs_resps):
                    item = code_map.get(code, {"code": code})
                    try:
                        if price_resp and not isinstance(price_resp, Exception):
                            out = price_resp.data if hasattr(price_resp, 'data') else None
                            if isinstance(out, dict):
                                item["stck_prpr"] = out.get("stck_prpr") or out.get('stck_clpr') or out.get('current_price')
                                item["prdy_ctrt"] = out.get("prdy_ctrt") or out.get('change_rate')
                                item["prdy_vrss"] = out.get("prdy_vrss") or out.get('change_price')
                        if mcap_resp and not isinstance(mcap_resp, Exception):
                            item["market_cap"] = getattr(mcap_resp, 'data', None) or (mcap_resp.data if hasattr(mcap_resp, 'data') else None)
                        if rs_resp and not isinstance(rs_resp, Exception):
                            # rs_resp may be ResCommonResponse or plain value
                            val = None
                            if hasattr(rs_resp, 'data'):
                                val = rs_resp.data
                            elif isinstance(rs_resp, (int, float)):
                                val = rs_resp
                            item["rs_rating"] = val or 0
                    except Exception:
                        pass
                    collected.append(item)

                # rate-limit sleep
                await asyncio.sleep(self.CHUNK_SLEEP_SEC)

            # sort by rs_rating desc
            try:
                collected.sort(key=lambda x: float(x.get('rs_rating') or 0), reverse=True)
            except Exception:
                pass

            self._minervini_stage2_cache = collected
            self._updated_at = datetime.now()
            elapsed = time.time() - start_time
            self._logger.info(f"Minervini Stage2 갱신 완료: {len(collected)}개, 소요: {elapsed:.1f}s")
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.BACKGROUND, NotificationLevel.INFO, "Minervini S2 갱신 완료", f"{len(collected)}개 수집, 소요: {elapsed:.1f}s")

        except Exception as e:
            self._logger.error(f"Minervini Stage2 갱신 실패: {e}", exc_info=True)
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "Minervini S2 갱신 실패", str(e))
        finally:
            self._is_refreshing = False

    async def get_minervini_stage2_cache(self, limit: int = 200):
        if not self._minervini_stage2_cache and not self._is_refreshing:
            try:
                asyncio.get_running_loop()
                asyncio.create_task(self.refresh_minervini_stage2())
            except RuntimeError:
                self._logger.warning("이벤트 루프 없음 — Minervini 즉시 갱신 스킵")

        return self._minervini_stage2_cache[:limit]

    async def force_collect(self) -> None:
        self._logger.info("MinerviniUpdateTask 강제 수집 요청")
        await self.refresh_minervini_stage2(force=True)
