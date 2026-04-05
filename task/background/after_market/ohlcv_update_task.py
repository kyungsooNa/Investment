# task/background/ohlcv_update_task.py
"""
장 마감 후 전체 종목의 OHLCV를 DB에 저장하는 백그라운드 태스크.
- 당일 OHLCV 및 전략에 필요한 최대 600일치 역사 데이터를 유지한다.
- DB에 이미 존재하는 날짜는 API를 호출하지 않아 불필요한 중복 요청을 방지한다.
"""
import asyncio
import logging
import time
import pandas as pd
import FinanceDataReader as fdr

from datetime import datetime, timedelta
from typing import List, Dict, Optional, TYPE_CHECKING
from common.types import ErrorCode
from core.performance_profiler import PerformanceProfiler
from core.market_clock import MarketClock
from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskState
from repositories.stock_repository import StockRepository
from repositories.stock_code_repository import StockCodeRepository
from services.market_calendar_service import MarketCalendarService
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

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

    TARGET_OHLCV_DAYS = 600   # 전략에서 최대 600일치를 사용하므로 동일하게 유지
    API_CHUNK_SIZE = 4        # [Tier 3] 증권사 API Fallback 병렬 처리 시 사용
    CHUNK_SLEEP_SEC = 1.5     # [Tier 3] 증권사 API Fallback 레이트 리밋 대기 시간
    CANARY_STOCKS = ["005930", "000660", "035420", "005380", "068270"]
    DB_UPSERT_BATCH_SIZE = 500
    
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
        """장마감 후 자동 스케줄러 시작."""
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._suspend_event.set()

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
        """OHLCV 데이터를 3-Tier(FDR 당일 일괄 -> FDR 과거 백필 -> API Fallback) 구조로 수집한다."""
        if not force and self._mcs and await self._mcs.is_market_open_now():
            self._logger.info("장 운영 중이므로 OHLCV 수집을 건너뜁니다.")
            return

        if self._is_collecting:
            self._logger.info("OHLCV 수집 이미 진행 중 — 스킵")
            return

        target_date = await self._mcs.get_latest_trading_date() if self._mcs else None
        if not target_date:
            self._logger.error("최근 거래일을 확인할 수 없어 OHLCV 수집을 중단합니다.")
            return

        if not force and self._last_collected_date == target_date:
            self._logger.info(f"이미 {target_date} OHLCV 수집 완료 — 스킵")
            return

        t_start_total = self._pm.start_timer()
        self._is_collecting = True
        start_time = time.time()
        all_stocks = self._load_all_stocks()

        self._progress = {
            "running": True, "force": force, "processed": 0, "total": len(all_stocks),
            "updated": 0, "skipped": 0, "elapsed": 0.0, "status": "초기화 중..."
        }

        try:
            self._logger.info(f"전체 종목 OHLCV 수집 파이프라인 시작 (기준일: {target_date})")

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Tier 1] 매일 수행되는 '당일 캔들' 일괄 업데이트 (FDR)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            self._progress["status"] = "당일 캔들 일괄 수집 중 (FDR)..."
            today_updated = await self._try_daily_bulk_via_fdr(target_date, start_time)
            
            if today_updated:
                self._logger.info("당일 기준 OHLCV 일괄 업데이트(FDR) 완료.")
            else:
                self._logger.warning("FDR 당일 업데이트 실패. 개별 수집으로 Fallback 합니다.")

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Tier 2 & 3] 과거 데이터가 부족한 종목 필터링 및 백필 (Backfill)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            self._progress["status"] = "과거 데이터 부족 종목 점검 중..."
            needs_backfill_stocks = []

            for code, name, market in all_stocks:
                if force:
                    needs_backfill_stocks.append((code, name, market))
                    continue

                summary = await self._stock_repo.get_ohlcv_summary(code)
                latest_date = summary.get("latest_date")
                total_count = summary.get("count", 0)

                # 당일 갱신에 실패했거나, DB가 거의 비어있는 경우(최초 실행 시)에만 백필
                # (주의: 600으로 두면 상장한지 2년 안된 종목들은 평생 백필을 돕니다)
                if latest_date != target_date or total_count < 10:
                    needs_backfill_stocks.append((code, name, market))

            if not needs_backfill_stocks:
                # 99%의 날에는 여기서 3초 만에 종료됨
                await self._finish_collection(target_date, start_time, t_start_total, "FDR Daily Bulk")
                return

            self._logger.info(f"과거 데이터 보완이 필요한 종목: {len(needs_backfill_stocks)}개")
            await self._backfill_historical_data(needs_backfill_stocks, target_date, force, start_time)
            
            await self._finish_collection(target_date, start_time, t_start_total, "FDR/API Backfill")

        except Exception as e:
            self._logger.error(f"OHLCV 파이프라인 수집 실패: {e}", exc_info=True)
            if self._ns:
                await self._ns.emit(NotificationCategory.BACKGROUND, NotificationLevel.ERROR, "OHLCV 파이프라인 실패", str(e))
        finally:
            self._is_collecting = False
            self._progress["running"] = False

    # ── 2. 수집 티어 구현 ─────────────────────────────────────────

    async def _try_daily_bulk_via_fdr(self, target_date: str, start_time: float) -> bool:
        """[Tier 1] FDR을 이용해 전 종목의 '당일' 캔들 1개를 일괄 수집 후 저장한다."""
        def _fetch_sync():
            df = fdr.StockListing('KRX')
            if df is None or df.empty: return None
            return df

        try:
            df_fdr = await asyncio.to_thread(_fetch_sync)
            if df_fdr is None: return False

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # ★ 방어막: DB에 넣기 전에 무조건 카나리아 테스트 진행
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            if not await self._verify_crawler_data(df_fdr, "FDR Daily Bulk"):
                return False

            records = self._format_fdr_listing_to_ohlcv_records(df_fdr, target_date)
            if not records: return False

            await self._save_bulk_to_db_with_progress(target_date, records, start_time)
            return True
        except Exception as e:
            self._logger.error(f"FDR 당일 일괄 수집 중 오류 발생: {e}")
            return False
        
    async def _backfill_historical_data(self, stocks: List[tuple], target_date: str, force: bool, start_time: float) -> None:
        """[Tier 2 & 3] 과거 데이터가 부족한 종목들을 FDR(고속) 또는 API로 '병렬' 채워넣는다."""
        total = len(stocks)
        processed = 0
        updated = 0
        
        # [수정 후] 600 '영업일'을 확보하려면 캘린더 일수는 주말/공휴일 포함 약 1.5배~2배가 필요합니다.
        clean_target_date = target_date.replace("-", "")
        start_date_obj = datetime.strptime(clean_target_date, "%Y%m%d") - timedelta(days=self.TARGET_OHLCV_DAYS * 2)
        start_date_str = start_date_obj.strftime("%Y-%m-%d")

        # FDR은 증권사 API보다 트래픽 제한이 널널하므로 청크 사이즈를 키웁니다.
        FDR_CHUNK_SIZE = 15

        async def process_single_stock(code: str, name: str) -> bool:
            """단일 종목에 대한 FDR 수집 및 자체 검증, API Fallback을 수행하는 비동기 워커"""
            is_success = False
            try:
                def _fetch_fdr():
                    return fdr.DataReader(code, start_date_str)
                
                # 백그라운드 스레드에서 FDR 호출
                df_fdr = await asyncio.to_thread(_fetch_fdr)
                
                if not df_fdr.empty:
                    records = self._format_fdr_to_ohlcv_records(code, df_fdr)
                    if records:
                        latest_record_fdr = records[-1] 
                        db_data = await self._stock_repo.get_stock_data(code, ohlcv_limit=1, caller="SanityCheck")
                        
                        is_sanity_passed = False
                        if db_data and db_data.get("ohlcv"):
                            db_latest_record = db_data["ohlcv"][-1]
                            db_date = db_latest_record.get("date", "").replace("-", "")
                            
                            # 날짜가 같을 때만 종가 대조
                            if db_date == clean_target_date and latest_record_fdr['close'] != db_latest_record['close']:
                                self._logger.warning(f"[{name}] FDR 과거 데이터 수정주가 오류 의심! Tier 3로 우회합니다.")
                            else:
                                is_sanity_passed = True
                        else:
                            is_sanity_passed = True

                        # 검증 통과 시 저장
                        if is_sanity_passed:
                            await self._stock_repo.upsert_ohlcv(records) 
                            is_success = True
            except Exception as e:
                self._logger.debug(f"[{name}] FDR 과거 데이터 수집 예외 발생: {e}")

            # [Tier 3] FDR 실패 또는 자체 검증 실패 시 개별 증권사 API로 우회
            if not is_success:
                api_result = await self._update_stock_ohlcv(code, target_date, force=force)
                if api_result:
                    is_success = True
                    
            return is_success

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ★ 핵심: 청크 단위로 분할하여 병렬(asyncio.gather) 실행
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        for chunk in _chunked(stocks, FDR_CHUNK_SIZE):
            await self._suspend_event.wait()
            self._progress["status"] = "과거 데이터 병렬 보완 중..."

            # 1. 병렬 실행
            tasks = [process_single_stock(code, name) for code, name, _ in chunk]
            results = await asyncio.gather(*tasks)

            # 2. 결과 집계 및 진행률 업데이트
            processed += len(chunk)
            updated += sum(1 for r in results if r)
            elapsed = time.time() - start_time  # 변수로 할당

            self._progress.update({
                "processed": self._progress["total"] - total + processed,
                "updated": updated,
                "elapsed": round(elapsed, 1) # progress dict에는 float 값으로 유지
            })

            # 로그 출력 (f-string 포맷팅으로 완벽 통일)
            if processed % 60 == 0 or processed >= total:
                self._logger.info(
                    f"과거 데이터 보완 진행: {processed}/{total} "
                    f"({processed / total * 100:.1f}%) "
                    f"| 갱신: {updated} | 소요: {elapsed:.1f}s"
                )

            # 너무 빠른 HTTP 요청을 방지하기 위한 가벼운 대기
            await asyncio.sleep(0.05)

    async def _update_stock_ohlcv(self, code: str, target_date: str, force: bool = False) -> Optional[bool]:
        """[Tier 3 Fallback] 증권사 API를 이용한 개별 종목 OHLCV 수집"""
        try:
            resp = await self._stock_query_service.get_ohlcv(code, caller="OhlcvUpdateTask")
            if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                return True
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.warning(f"API OHLCV 업데이트 실패 ({code}): {e}")
            return None

    # ── 3. 진행률 및 데이터 포맷 헬퍼 ──────────────────────────────────

    async def _save_bulk_to_db_with_progress(self, target_date: str, records: List[Dict], start_time: float) -> None:
        """크롤링된 전체 레코드를 DB에 Batch 단위로 저장하며 진행률을 업데이트한다."""
        total_records = len(records)
        if total_records == 0: return

        processed = 0
        for i in range(0, total_records, self.DB_UPSERT_BATCH_SIZE):
            batch = records[i:i + self.DB_UPSERT_BATCH_SIZE]
            
            await self._stock_repo.upsert_ohlcv(batch)
            
            processed += len(batch)
            self._progress.update({
                "processed": processed,
                "updated": processed,
                "elapsed": round(time.time() - start_time, 1),
                "status": f"DB 저장 중... ({processed}/{total_records})"
            })
            await asyncio.sleep(0.01)

    def _format_fdr_listing_to_ohlcv_records(self, df: pd.DataFrame, target_date: str) -> List[Dict]:
        """FDR StockListing 당일 데이터를 DB OHLCV 레코드 포맷으로 변환"""
        records = []
        # 필터링을 위해 마스터 목록 로드
        valid_codes = {code for code, _, _ in self._load_all_stocks()}

        for _, row in df.iterrows():
            code = str(row.get('Code', '')).zfill(6)
            if not code or code not in valid_codes: continue
            
            try:
                records.append({
                    "code": code,
                    "date": target_date,
                    "open": int(row.get('Open', 0)),
                    "high": int(row.get('High', 0)),
                    "low": int(row.get('Low', 0)),
                    "close": int(row.get('Close', 0)),
                    "volume": int(row.get('Volume', 0)),
                    "trading_value": int(row.get('Amount', 0)), 
                })
            except (ValueError, TypeError):
                continue
        return records

    def _format_fdr_to_ohlcv_records(self, code: str, df: pd.DataFrame) -> List[Dict]:
        """FDR 시계열 과거 데이터를 DB OHLCV 레코드 포맷으로 변환"""
        records = []
        for date_idx, row in df.iterrows():
            # [수정 후] target_date(YYYYMMDD)와 DB Primary Key 포맷을 완벽하게 통일
            date_str = date_idx.strftime("%Y%m%d")
            try:
                records.append({
                    "code": code,
                    "date": date_str,
                    "open": int(row.get('Open', 0)),
                    "high": int(row.get('High', 0)),
                    "low": int(row.get('Low', 0)),
                    "close": int(row.get('Close', 0)),
                    "volume": int(row.get('Volume', 0)),
                    # FDR 과거 데이터에는 'Amount' 필드가 없어 종가 * 거래량으로 근사치 추정
                    "trading_value": int(row.get('Close', 0)) * int(row.get('Volume', 0)), 
                })
            except (ValueError, TypeError):
                continue
        return records

    async def _finish_collection(self, target_date: str, start_time: float, total_start_time: float, source: str) -> None:
        """수집 완료 후 공통 후처리 로직"""
        self._last_collected_date = target_date
        elapsed = time.time() - start_time
        
        self._logger.info(
            f"전체 종목 OHLCV 수집 완료 (경로: {source}) | "
            f"갱신: {self._progress['updated']} / 스킵: {self._progress['skipped']} | 소요: {elapsed:.1f}s"
        )
        
        self._pm.log_timer("OhlcvUpdateTask._collect_all_ohlcv", total_start_time, threshold=10.0)
        
        if self._ns:
            await self._ns.emit(
                NotificationCategory.BACKGROUND, NotificationLevel.INFO, 
                "전체 종목 OHLCV 수집 완료",
                f"소스: {source} / 소요: {elapsed:.1f}초"
            )

    # ── 내부 헬퍼 ─────────────────────────────────────────
    async def _verify_crawler_data(self, df_crawled: pd.DataFrame, source_name: str) -> bool:
        """증권사 API의 확정 데이터(시/고/저/종가)와 크롤링 데이터가 일치하는지 완벽 검증한다."""
        self._logger.info(f"[{source_name}] 당일 데이터 정합성 검증(OHLC 4종) 시작...")
        
        for code in self.CANARY_STOCKS:
            # 증권사 API 호출 (현재가/당일시세 API 사용)
            api_resp = await self._stock_query_service.get_current_price(code, count_stats=False, caller="OhlcvUpdateTask")
            if not api_resp or api_resp.rt_cd != ErrorCode.SUCCESS.value:
                self._logger.warning(f"검증용 API 호출 실패({code}). 검증을 건너뛰고 실패 처리합니다.")
                return False
                
            data = api_resp.data
            output = data.get('output') if isinstance(data, dict) else data
            
            def _get_api_val(key):
                val = output.get(key, 0) if isinstance(output, dict) else getattr(output, key, 0)
                return int(val) if val else 0

            api_close = _get_api_val('stck_prpr')
            api_open = _get_api_val('stck_oprc')
            api_high = _get_api_val('stck_hgpr')
            api_low = _get_api_val('stck_lwpr')
            
            try:
                # FDR StockListing DataFrame에서 검증용 종목 데이터 추출
                row = df_crawled[df_crawled['Code'] == code].iloc[0]
                
                crawled_close = int(row.get('Close', 0))
                crawled_open = int(row.get('Open', 0))
                crawled_high = int(row.get('High', 0))
                crawled_low = int(row.get('Low', 0))
                
            except (KeyError, IndexError):
                self._logger.warning(f"크롤링 데이터에 검증용 종목({code})이 없거나 파싱할 수 없습니다.")
                return False

            # 시/고/저/종 4개 값 모두 대조
            if (api_close != crawled_close or api_open != crawled_open or 
                api_high != crawled_high or api_low != crawled_low):
                
                self._logger.warning(
                    f"데이터 불일치 감지! ({code}) 거래소 업데이트 지연 또는 라이브러리 오류입니다.\n"
                    f" - API   : 시({api_open}) 고({api_high}) 저({api_low}) 종({api_close})\n"
                    f" - {source_name.ljust(6)}: 시({crawled_open}) 고({crawled_high}) 저({crawled_low}) 종({crawled_close})"
                )
                return False

        self._logger.info(f"[{source_name}] 데이터 정합성 검증 완벽 통과!")
        return True


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
