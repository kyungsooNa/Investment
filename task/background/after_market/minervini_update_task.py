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

    # 가격 데이터 수집 완료로 간주할 최소 종목 수 (KOSPI+KOSDAQ ~2500개의 약 20%)
    MIN_PRICE_COUNT = 500

    def __init__(
        self,
        minervini_service,
        stock_code_repository: StockCodeRepository = None,
        stock_repository=None,
        stock_query_service=None,
        broker_api_wrapper=None,
        rs_rating_service=None,
        market_clock: MarketClock = None,
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        telegram_reporter=None,
        market_calendar_service=None,
        daily_price_collector_task=None,
        worker_pool=None,
    ):
        super().__init__(mcs=market_calendar_service, market_clock=market_clock, logger=logger or logging.getLogger(__name__), worker_pool=worker_pool)
        self._minervini = minervini_service
        self._daily_price_collector_task = daily_price_collector_task
        self.stock_code_repository = stock_code_repository
        self._stock_repo = stock_repository
        self._sqs = stock_query_service
        self._broker = broker_api_wrapper
        self._rs_svc = rs_rating_service
        self.pm = performance_profiler or PerformanceProfiler(enabled=False)
        self._notification_service = notification_service
        self._telegram_reporter = telegram_reporter
        self._suspend_event: asyncio.Event = asyncio.Event()
        self._suspend_event.set()

        self._minervini_stage2_cache: List[Dict] = []
        self._updated_at: Optional[datetime] = None
        self._is_refreshing = False
        self._progress: Dict = {
            "running": False,
            "processed": 0,
            "total": 0,
            "collected": 0,
            "elapsed": 0.0,
            "status": "",
        }

    def set_daily_price_collector_task(self, daily_price_collector_task) -> None:
        """DailyPriceCollectorTask 후주입 — 생성 순서 의존 해소용 (WiringPhase 에서 호출)."""
        self._daily_price_collector_task = daily_price_collector_task

    @property
    def task_name(self) -> str:
        return "minervini_update"

    @property
    def _scheduler_label(self) -> str:
        return "MinerviniUpdateTask"

    async def _on_start_hook(self) -> None:
        self._suspend_event.set()

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

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        latest_trading_date_dt = datetime.strptime(latest_trading_date, '%Y%m%d').date()
        needs = (not self._updated_at) or (self._updated_at.date() != latest_trading_date_dt)
        if needs:
            await self.refresh_minervini_stage2()

    def _load_all_stocks(self) -> List[tuple]:
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

            if not force and self._updated_at and target_date and self._updated_at.strftime('%Y%m%d') == target_date:
                self._logger.info(f"이미 {target_date} Minervini Stage2 갱신 완료 — 스킵")
                self._is_refreshing = False
                return

            # ── 사전 체크 1: 가격 데이터가 DB에 있는지 확인 ──────────────────────
            if target_date and self._stock_repo:
                price_count = await self._stock_repo.get_count_by_date(target_date)
                if price_count < self.MIN_PRICE_COUNT:
                    self._logger.info(
                        f"[MinerviniUpdate] {target_date} 가격 데이터 부족 ({price_count}개) "
                        f"— DailyPriceCollectorTask 먼저 실행"
                    )
                    if self._daily_price_collector_task:
                        dpc = self._daily_price_collector_task
                        if dpc._is_collecting:
                            self._logger.info("[MinerviniUpdate] DailyPriceCollector 수집 진행 중 — 완료 대기")
                            await dpc._collection_done_event.wait()
                        else:
                            await dpc.force_run()
                    else:
                        self._logger.warning("[MinerviniUpdate] DailyPriceCollectorTask 미설정 — 가격 데이터 없이 진행")

            # ── 사전 체크 2: Stage 데이터가 이미 DB에 있는지 확인 ────────────────
            if not force and target_date and self._stock_repo:
                stage_count = await self._stock_repo.get_minervini_stage_count(target_date)
                if stage_count > 0:
                    self._logger.info(
                        f"[MinerviniUpdate] {target_date} Stage 데이터 이미 존재 ({stage_count}개) "
                        f"— DB에서 캐시 로드 후 스킵"
                    )
                    db_stage2 = await self._stock_repo.get_minervini_stage2_stocks(target_date)
                    if db_stage2:
                        self._minervini_stage2_cache = [
                            {
                                "code": it.get("code", ""),
                                "name": it.get("name", ""),
                                "stck_prpr": str(it.get("current_price") or 0),
                                "prdy_ctrt": str(it.get("change_rate") or 0),
                                "stage": it.get("minervini_stage", 2),
                                "rs_rating": it.get("rs_rating") or 0,
                                "market_cap": it.get("market_cap") or 0,
                            }
                            for it in db_stage2
                        ]
                        self._updated_at = datetime.now()
                    self._is_refreshing = False
                    return

            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            collected: List[Dict] = []

            # initialize progress
            self._progress.update({
                "running": True,
                "processed": 0,
                "total": total,
                "collected": 0,
                "elapsed": 0.0,
                "status": "Minervini Stage 판정 및 정보 수집 중...",
            })

            processed = 0
            # accumulate stage info for ALL stocks across chunks
            all_code_map = {}
            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                await self._suspend_event.wait()

                # 1) 우선 Stage 판정만 호출
                tasks = [self._minervini.get_stage_for_code(code) for code, _, _ in chunk]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                # 2) Stage2인 종목만 후속 정보 수집
                stage2_codes = []
                code_map = {}
                # populate stage info for ALL codes in this chunk
                for (code, name, market), resp in zip(chunk, responses):
                    stage = None
                    reason = ""
                    if isinstance(resp, Exception):
                        stage = None
                    else:
                        try:
                            if isinstance(resp, (list, tuple)):
                                stage = int(resp[0])
                                reason = str(resp[1]) if len(resp) > 1 else ""
                            else:
                                stage = int(resp)
                                reason = ""
                        except Exception:
                            stage = None
                    stg = int(stage) if stage is not None else 0
                    code_map[code] = {"code": code, "name": name, "stage": stg, "reason": reason, "market": market}
                    if stg == 2:
                        stage2_codes.append(code)

                # merge chunk map into global all_code_map for persistence later
                for k, v in code_map.items():
                    all_code_map[k] = v

                # 3) Stage2 종목의 현재가/시가총액/RS 병렬 수집
                follow_tasks = []
                for code in stage2_codes:
                    # get_current_price via stock_query_service (if available)
                    if self._sqs:
                        follow_tasks.append(self._sqs.get_current_price(code, caller="MinerviniUpdateTask"))
                    else:
                        follow_tasks.append(asyncio.sleep(0, result=None))

                follow_resps = await asyncio.gather(*follow_tasks, return_exceptions=True)

                # RS collection (best-effort)
                rs_tasks = []
                for code in stage2_codes:
                    if self._rs_svc:
                        rs_tasks.append(self._rs_svc.get_rating(code))
                    else:
                        rs_tasks.append(asyncio.sleep(0, result=None))

                rs_resps = await asyncio.gather(*rs_tasks, return_exceptions=True)

                # assemble
                for code, price_resp, rs_resp in zip(stage2_codes, follow_resps, rs_resps):
                    item = code_map.get(code, {"code": code})
                    try:
                        if price_resp and not isinstance(price_resp, Exception):
                            if hasattr(price_resp, 'data'):
                                out = price_resp.data
                            elif isinstance(price_resp, dict):
                                out = price_resp.get('output') or price_resp
                            else:
                                out = None
                            # unwrap {'output': ResStockFullInfoApiOutput, ...} dict
                            if isinstance(out, dict) and 'output' in out:
                                out = out['output']
                            if out is not None:
                                get_field = out.get if isinstance(out, dict) else lambda k: getattr(out, k, None)
                                item["stck_prpr"] = get_field("stck_prpr") or get_field("stck_clpr") or get_field("current_price")
                                item["prdy_ctrt"] = get_field("prdy_ctrt") or get_field("change_rate")
                                item["prdy_vrss"] = get_field("prdy_vrss") or get_field("change_price")
                                item["market_cap"] = get_field("hts_avls") or get_field("market_cap")
                        if rs_resp and not isinstance(rs_resp, Exception):
                            val = 0
                            try:
                                if hasattr(rs_resp, 'data') and rs_resp.data is not None:
                                    d = rs_resp.data
                                    if hasattr(d, 'rs_rating'):
                                        val = int(d.rs_rating)
                                    elif isinstance(d, (int, float)):
                                        val = int(d)
                                elif isinstance(rs_resp, (int, float)):
                                    val = int(rs_resp)
                            except (TypeError, ValueError):
                                val = 0
                            item["rs_rating"] = val
                    except Exception:
                        pass
                    collected.append(item)
                    # also ensure the global map reflects any enriched fields
                    all_code_map[code] = item

                # rate-limit sleep
                await asyncio.sleep(self.CHUNK_SLEEP_SEC)

                # update progress after each chunk
                processed += len(chunk)
                elapsed = time.time() - start_time
                self._progress.update({
                    "processed": processed,
                    "collected": len(collected),
                    "elapsed": round(elapsed, 1),
                })

            # sort by rs_rating desc
            try:
                collected.sort(key=lambda x: float(x.get('rs_rating') or 0), reverse=True)
            except Exception:
                pass

            self._minervini_stage2_cache = collected
            self._updated_at = datetime.now()
            # mark progress complete
            elapsed = time.time() - start_time
            self._progress.update({
                "running": False,
                "processed": total,
                "collected": len(collected),
                "elapsed": round(elapsed, 1),
                "status": "완료",
            })
            self._logger.info(f"Minervini Stage2 갱신 완료: {len(collected)}개, 소요: {elapsed:.1f}s")
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.BACKGROUND, NotificationLevel.INFO, "Minervini S2 갱신 완료", f"{len(collected)}개 수집, 소요: {elapsed:.1f}s")

            # Telegram report (if reporter available)
            try:
                if getattr(self, '_telegram_reporter', None):
                    # use trade_date if available, else formatted updated_at
                    report_date = target_date or (self._updated_at.strftime('%Y%m%d') if self._updated_at else datetime.now().strftime('%Y%m%d'))
                    # send top N (already sorted by rs)
                    await self._telegram_reporter.send_minervini_report(collected, report_date)
            except Exception as e:
                self._logger.warning(f"Telegram 리포트 전송 실패: {e}")

            # Persist minervini stage info into daily snapshot DB (best-effort)
            # update_minervini_fields만 호출하여 DailyPriceCollectorTask의
            # INSERT OR REPLACE가 해당 컬럼을 NULL로 덮어쓰는 문제를 방지한다.
            try:
                if self._stock_repo:
                    trade_date = target_date or datetime.now().strftime('%Y%m%d')

                    records = []
                    for it in all_code_map.values():
                        records.append({
                            "code": it.get("code"),
                            "minervini_stage": int(it.get("stage") or 0),
                            "minervini_reason": it.get("reason") or None,
                            "rs_rating": it.get("rs_rating") or None,
                        })
                    if records:
                        await self._stock_repo.update_minervini_fields(trade_date, records)
            except Exception as e:
                self._logger.warning(f"MinerviniUpdateTask DB에 쓰기 실패: {e}")

        except Exception as e:
            self._logger.error(f"Minervini Stage2 갱신 실패: {e}", exc_info=True)
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "Minervini S2 갱신 실패", str(e))
        finally:
            self._is_refreshing = False
            # ensure running flag is cleared on any exit
            try:
                self._progress["running"] = False
            except Exception:
                pass

    async def get_minervini_stage2_cache(self, limit: int = 200):
        if not self._minervini_stage2_cache and not self._is_refreshing:
            try:
                asyncio.get_running_loop()
                asyncio.create_task(self.refresh_minervini_stage2())
            except RuntimeError:
                self._logger.warning("이벤트 루프 없음 — Minervini 즉시 갱신 스킵")

        return self._minervini_stage2_cache[:limit]

    async def force_run(self) -> None:
        self._logger.info("MinerviniUpdateTask 강제 수집 요청")
        async with self._running_state():
            if self._is_refreshing:
                self._logger.info("Minervini 갱신 이미 진행 중 — 완료 대기 후 반환")
                return
            await self.refresh_minervini_stage2(force=True)

    def get_progress(self) -> dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현)."""
        p = dict(self._progress)
        p["last_updated"] = self._updated_at.strftime('%Y-%m-%d %H:%M:%S') if self._updated_at else None
        return p
