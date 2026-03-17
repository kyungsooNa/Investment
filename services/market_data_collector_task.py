# services/market_data_collector_task.py
"""
장 마감 후 전체 종목 현재가를 수집하여 DB에 저장하는 백그라운드 태스크.
get_current_price API를 사용하여 종목별 50+ 필드(시가/고가/저가/현재가/PER/PBR 등)를 수집한다.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional, TYPE_CHECKING

from brokers.broker_api_wrapper import BrokerAPIWrapper
from common.types import ErrorCode
from core.performance_manager import PerformanceManager
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from managers.market_data_repository import MarketDataRepository
from managers.market_date_manager import MarketDateManager
from managers.notification_manager import NotificationManager
from market_data.stock_code_mapper import StockCodeMapper


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


# ETF/ETN 브랜드명 접두사 (RankingTask와 동일)
_ETF_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
    "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
    "VITA", "TREX", "MASTER", "WOORI", "KINDEX",
)


class MarketDataCollectorTask(SchedulableTask):
    """장 마감 후 전체 종목 현재가를 수집하여 DB에 저장하는 백그라운드 태스크."""

    API_CHUNK_SIZE = 8
    CHUNK_SLEEP_SEC = 1.1

    def __init__(
        self,
        broker_api_wrapper: BrokerAPIWrapper,
        stock_code_mapper: StockCodeMapper,
        repository: MarketDataRepository,
        market_date_manager: Optional[MarketDateManager] = None,
        performance_manager: Optional[PerformanceManager] = None,
        notification_manager: Optional[NotificationManager] = None,
        logger=None,
    ):
        self._broker = broker_api_wrapper
        self._mapper = stock_code_mapper
        self._repo = repository
        self._mdm = market_date_manager
        self._pm = performance_manager or PerformanceManager(enabled=False)
        self._nm = notification_manager
        self._logger = logger or logging.getLogger(__name__)

        # SchedulableTask 상태
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._suspend_event: asyncio.Event = asyncio.Event()
        self._suspend_event.set()  # 초기에는 실행 가능

        # 수집 상태
        self._is_collecting: bool = False
        self._last_collected_date: Optional[str] = None
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
        return "market_data_collector"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        """수집 1회 실행 + 장마감 후 자동 스케줄러 시작."""
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._suspend_event.set()

        self._tasks.append(
            asyncio.create_task(self._collect_all_prices())
        )
        self._tasks.append(
            asyncio.create_task(self._after_market_scheduler())
        )
        self._logger.info(f"MarketDataCollectorTask 시작: {len(self._tasks)}개 태스크")

    async def stop(self) -> None:
        """모든 태스크를 취소하고 정리한다."""
        self._logger.info(f"MarketDataCollectorTask 종료 시작: {len(self._tasks)}개 태스크")
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._logger.info("MarketDataCollectorTask 종료 완료")

    async def suspend(self) -> None:
        """수집을 일시 중지한다 (chunk 사이에서 대기)."""
        if self._state == TaskState.RUNNING:
            self._suspend_event.clear()
            self._state = TaskState.SUSPENDED
            self._logger.info("MarketDataCollectorTask 일시 중지")

    async def resume(self) -> None:
        """일시 중지된 수집을 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._suspend_event.set()
            self._state = TaskState.RUNNING
            self._logger.info("MarketDataCollectorTask 재개")

    # ── 장마감 후 자동 스케줄러 ────────────────────────────

    async def _after_market_scheduler(self) -> None:
        """장 마감 후 자동으로 수집을 스케줄링하는 루프."""
        self._logger.info("MarketDataCollector 장마감 후 자동 수집 스케줄러 시작")

        while True:
            try:
                # 1. 장 중이면 장 마감까지 대기
                if self._mdm and await self._mdm.is_market_open_now():
                    self._logger.info("장 운영 중 — 마감 후 수집 대기")
                    await asyncio.sleep(300)  # 5분마다 재확인
                    continue

                # 2. 장 마감 이후 — 수집 필요 여부 판단
                latest_trading_date = (
                    await self._mdm.get_latest_trading_date() if self._mdm else None
                )

                if latest_trading_date:
                    needs_collect = (
                        self._last_collected_date != latest_trading_date
                    )
                    if needs_collect:
                        await self._collect_all_prices()

                self._logger.info(
                    "MarketDataCollector: 오늘 수집 완료 또는 휴장. 12시간 대기."
                )
                await asyncio.sleep(12 * 3600)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(
                    f"MarketDataCollector 스케줄러 오류: {e}", exc_info=True
                )
                await asyncio.sleep(60)

    # ── 전체 종목 현재가 수집 ────────────────────────────

    async def _collect_all_prices(self) -> None:
        """전체 종목 현재가를 수집하여 DB에 저장한다."""
        # 장 중에는 수집하지 않음
        if self._mdm and await self._mdm.is_market_open_now():
            self._logger.info("장 운영 중이므로 현재가 수집을 건너뜁니다.")
            return

        if self._is_collecting:
            self._logger.info("현재가 수집 이미 진행 중 — 스킵")
            return

        t_start_total = self._pm.start_timer()
        self._is_collecting = True
        start_time = time.time()

        # 기준일 확인
        target_date = None
        if self._mdm:
            target_date = await self._mdm.get_latest_trading_date()

        if not target_date:
            self._logger.error("최근 거래일을 확인할 수 없어 현재가 수집을 중단합니다.")
            self._is_collecting = False
            return

        # 이미 수집한 날짜인지 확인
        if self._last_collected_date == target_date:
            self._logger.info(f"이미 {target_date} 현재가 수집 완료 — 스킵")
            self._is_collecting = False
            return

        self._logger.info(f"전체 종목 현재가 수집 시작 (기준일: {target_date})")
        self._progress = {
            "running": True, "processed": 0, "total": 0,
            "collected": 0, "elapsed": 0.0,
        }

        try:
            # 1. 전체 종목 로드
            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            self._progress["total"] = total
            self._logger.info(f"현재가 수집: 전체 {total}개 종목 순회 시작")

            # 2. 청크별 수집
            collected_records: List[Dict] = []
            processed = 0

            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                # suspend 체크포인트
                await self._suspend_event.wait()

                # 병렬 API 호출
                tasks = [
                    self._fetch_with_retry(code)
                    for code, _, _ in chunk
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                # 결과 처리
                batch_records = []
                for (code, name, market), resp in zip(chunk, responses):
                    if isinstance(resp, Exception):
                        continue
                    record = self._extract_record(code, name, market, resp)
                    if record:
                        batch_records.append(record)

                # 배치 단위로 DB 저장
                if batch_records:
                    self._repo.upsert_prices(target_date, batch_records)
                    collected_records.extend(batch_records)

                processed += len(chunk)
                elapsed = time.time() - start_time
                self._progress.update({
                    "processed": processed,
                    "collected": len(collected_records),
                    "elapsed": round(elapsed, 1),
                })

                if processed % 50 == 0 or processed >= total:
                    self._logger.info(
                        f"현재가 수집 진행: {processed}/{total} "
                        f"({processed / total * 100:.1f}%) "
                        f"| 수집: {len(collected_records)} | 소요: {elapsed:.1f}s"
                    )

                # 전체 캐시 HIT면 sleep 불필요
                all_cache_hit = all(
                    getattr(r, '_cache_hit', False)
                    for r in responses if not isinstance(r, Exception)
                )
                if not all_cache_hit:
                    await asyncio.sleep(self.CHUNK_SLEEP_SEC)

            # 3. 완료 처리
            self._last_collected_date = target_date
            elapsed = time.time() - start_time
            self._logger.info(
                f"전체 종목 현재가 수집 완료: {len(collected_records)}개 종목, "
                f"소요: {elapsed:.1f}s"
            )
            self._pm.log_timer(
                "MarketDataCollectorTask._collect_all_prices",
                t_start_total, threshold=10.0,
            )
            if self._nm:
                await self._nm.emit(
                    "API", "info", "전체 종목 현재가 수집 완료",
                    f"{len(collected_records)}개 종목 수집, 소요: {elapsed:.1f}초",
                )

        except Exception as e:
            self._logger.error(f"현재가 수집 실패: {e}", exc_info=True)
            if self._nm:
                await self._nm.emit("SYSTEM", "error", "현재가 수집 실패", str(e))
        finally:
            self._is_collecting = False
            self._progress["running"] = False

    # ── 내부 헬퍼 ─────────────────────────────────────────

    async def _fetch_with_retry(self, code: str):
        """get_current_price API 호출 + 재시도."""
        max_retries = 3
        delay = 1.0
        for attempt in range(max_retries):
            try:
                resp = await self._broker.get_current_price(code)
                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                    return resp
                error_msg = resp.msg1 if resp else "응답 없음"
                self._logger.warning(
                    f"현재가 조회 실패 (시도 {attempt + 1}/{max_retries}): "
                    f"{code}, 사유: {error_msg}"
                )
            except Exception as e:
                self._logger.error(
                    f"현재가 조회 예외 (시도 {attempt + 1}/{max_retries}): "
                    f"{code}, 오류: {e}"
                )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 1.5
        return None

    @staticmethod
    def _extract_record(
        code: str, name: str, market: str, resp
    ) -> Optional[Dict]:
        """API 응답에서 DB 저장용 레코드를 추출한다."""
        if not resp:
            return None

        try:
            data = resp.data
            if not data:
                return None

            # get_current_price의 응답 구조: data = {'output': ResStockFullInfoApiOutput}
            output = data.get('output') if isinstance(data, dict) else data
            if not output:
                return None

            def _safe_int(val, default=0):
                try:
                    return int(val) if val else default
                except (ValueError, TypeError):
                    return default

            def _safe_float(val, default=0.0):
                try:
                    return float(val) if val else default
                except (ValueError, TypeError):
                    return default

            # ResStockFullInfoApiOutput 필드 → DB 레코드 변환
            # output이 Pydantic 모델이면 getattr, dict면 get
            _get = (
                (lambda k, d=None: getattr(output, k, d))
                if hasattr(output, 'stck_prpr')
                else (lambda k, d=None: output.get(k, d))
            )

            return {
                "code": code,
                "name": name,
                "current_price": _safe_int(_get("stck_prpr")),
                "open_price": _safe_int(_get("stck_oprc")),
                "high_price": _safe_int(_get("stck_hgpr")),
                "low_price": _safe_int(_get("stck_lwpr")),
                "prev_close": _safe_int(_get("stck_sdpr")),
                "change_price": _safe_int(_get("prdy_vrss")),
                "change_sign": _get("prdy_vrss_sign", ""),
                "change_rate": _get("prdy_ctrt", "0"),
                "volume": _safe_int(_get("acml_vol")),
                "trading_value": _safe_int(_get("acml_tr_pbmn")),
                "market_cap": _safe_int(_get("hts_avls")),
                "per": _safe_float(_get("per")),
                "pbr": _safe_float(_get("pbr")),
                "eps": _safe_float(_get("eps")),
                "w52_high": _safe_int(_get("w52_hgpr")),
                "w52_low": _safe_int(_get("w52_lwpr")),
                "market": market,
            }
        except Exception:
            return None

    def _load_all_stocks(self) -> List[tuple]:
        """StockCodeMapper에서 KOSPI/KOSDAQ 전체 종목 로드 (ETF/우선주 제외)."""
        all_stocks = []
        for _, row in self._mapper.df.iterrows():
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
