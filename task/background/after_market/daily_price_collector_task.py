# task/background/daily_price_collector_task.py
"""
장 마감 후 전체 종목 현재가+펀더멘털을 수집하여 StockRepository에 저장하는 백그라운드 태스크.
get_current_price API를 사용하여 종목별 50+ 필드(시가/고가/저가/현재가/PER/PBR 등)를 수집한다.
"""
import asyncio
import logging
import time
import pandas as pd
import FinanceDataReader as fdr
import pykrx

from typing import Dict, List, Optional, TYPE_CHECKING
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


# ETF/ETN 브랜드명 접두사 (OhlcvUpdateTask와 동일)
_ETF_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
    "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
    "VITA", "TREX", "MASTER", "WOORI", "KINDEX",
)


class DailyPriceCollectorTask(AfterMarketTask):
    """장 마감 후 전체 종목 현재가+펀더멘털을 수집하여 StockRepository에 저장하는 백그라운드 태스크."""

    API_CHUNK_SIZE = 8
    CHUNK_SLEEP_SEC = 1.1
    DB_UPSERT_BATCH_SIZE = 500
    # 검증의 견고성을 위해 시장 대표성을 띄는 다수 종목(삼성전자, SK하이닉스, NAVER, 현대차, 셀트리온) 지정
    CANARY_STOCKS = ["005930", "000660", "035420", "005380", "068270"]

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
            "collected": 0,
            "elapsed": 0.0,
            "status": "",
        }
        self._all_stocks_cache = None

    # ── SchedulableTask 인터페이스 구현 ────────────────────────

    @property
    def task_name(self) -> str:
        return "daily_price_collector"

    @property
    def _scheduler_label(self) -> str:
        return "DailyPriceCollector"

    async def start(self) -> None:
        """장마감 후 자동 스케줄러 시작."""
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._suspend_event.set()

        self._tasks.append(
            asyncio.create_task(self._after_market_scheduler())
        )
        self._logger.info(f"DailyPriceCollectorTask 시작: {len(self._tasks)}개 태스크")

    async def suspend(self) -> None:
        """수집을 일시 중지한다 (chunk 사이에서 대기)."""
        if self._state == TaskState.RUNNING:
            self._suspend_event.clear()
            self._state = TaskState.SUSPENDED
            self._logger.info("DailyPriceCollectorTask 일시 중지")

    async def resume(self) -> None:
        """일시 중지된 수집을 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._suspend_event.set()
            self._state = TaskState.RUNNING
            self._logger.info("DailyPriceCollectorTask 재개")

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백: 해당 거래일의 수집이 필요하면 실행."""
        if self._last_collected_date != latest_trading_date:
            await self._collect_all_prices()

    # ── 전체 종목 현재가 수집 ────────────────────────────
    async def _collect_all_prices(self, force: bool = False) -> None:
        """전체 종목 현재가+펀더멘털을 3-Tier Fallback 구조로 수집한다."""
        if self._mcs and await self._mcs.is_market_open_now():
            self._logger.info("장 운영 중이므로 현재가 수집을 건너뜁니다.")
            return

        if self._is_collecting:
            self._logger.info("현재가 수집 이미 진행 중 — 스킵")
            return

        target_date = await self._mcs.get_latest_trading_date() if self._mcs else None
        if not target_date:
            self._logger.error("최근 거래일을 확인할 수 없어 현재가 수집을 중단합니다.")
            return

        if not force and self._last_collected_date == target_date:
            self._logger.info(f"이미 {target_date} 현재가 수집 완료 — 스킵")
            return

        self._logger.info(f"전체 종목 수집 파이프라인 시작 (기준일: {target_date})")
        self._is_collecting = True
        start_time = time.time()
        
        # 반복 조회를 피하기 위해 한 번 로드 후 캐싱
        self._all_stocks_cache = self._load_all_stocks()
        
        try:            
            # FinanceDataReader 제외: FinanceDataReader는 신고가 데이터(w52_high/w52_low)를 제공하지 않아 NewHighTask의 핵심 데이터 소스로 활용할 수 없습니다.
            # # [Tier 1] FinanceDataReader 시도
            # if await self._try_collect_via_fdr(target_date, start_time):
            #     await self._finish_collection(target_date, start_time, "FDR")
            #     return
            
            # # [Tier 2] 모두 실패 시 최후의 보루 증권사 API 청크 수집
            # self._logger.warning("크롤링 모두 실패. 증권사 API(Chunk) 수집으로 Fallback 합니다.")
            # if self._ns:
            #     await self._ns.emit(
            #         NotificationCategory.BACKGROUND, NotificationLevel.WARNING,
            #         "수집 모드 전환", "크롤링 라이브러리 오류로 인해 증권사 API 일일이 수집 모드(약 10분 소요)로 동작합니다."
            #     )
            await self._collect_via_broker_api(target_date, start_time)
            await self._finish_collection(target_date, start_time, "Broker API")

        except Exception as e:
            self._logger.error(f"전체 수집 파이프라인 실패: {e}", exc_info=True)
        finally:
            self._is_collecting = False
            self._all_stocks_cache = None
    
    # ── 2. 데이터 검증 (Sanity Check) ─────────────────────────────

    async def _verify_crawler_data(self, df_crawled: pd.DataFrame, source_name: str) -> bool:
        """
        증권사 API의 확정 데이터(시가/고가/저가/종가)와 크롤링 데이터가 일치하는지 완벽 검증한다.
        """
        self._logger.info(f"[{source_name}] 데이터 정합성 검증(OHLC 4종) 시작...")
        
        match_count = 0
        mismatch_count = 0
        
        for code in self.CANARY_STOCKS:
            # 1. 증권사 API에서 Source of Truth 호출
            api_resp = await self._fetch_with_retry(code)
            if not api_resp or api_resp.rt_cd != ErrorCode.SUCCESS.value:
                self._logger.debug(f"검증용 API 호출 실패({code}) - 스킵")
                continue
                
            # API 데이터 추출 (output 뎁스 고려)
            data = api_resp.data
            output = data.get('output') if isinstance(data, dict) else data
            
            # API 속성 추출 헬퍼
            def _get_api_val(key):
                val = output.get(key, 0) if isinstance(output, dict) else getattr(output, key, 0)
                return int(val) if val else 0

            # API: 종가(stck_prpr), 시가(stck_oprc), 고가(stck_hgpr), 저가(stck_lwpr)
            api_close = _get_api_val('stck_prpr')
            api_open = _get_api_val('stck_oprc')
            api_high = _get_api_val('stck_hgpr')
            api_low = _get_api_val('stck_lwpr')
            
            # 2. 크롤링 데이터(DataFrame)에서 추출
            try:
                # 인덱스가 종목코드인지, 컬럼에 종목코드가 있는지 확인
                if code in df_crawled.index:
                    row = df_crawled.loc[code]
                else:
                    matches = df_crawled[df_crawled['종목코드'] == code]
                    if matches.empty:
                        self._logger.debug(f"크롤링 데이터에 검증용 종목({code}) 없음 - 스킵")
                        continue
                    row = matches.iloc[0]
                    
                # 크롤링 데이터 추출 헬퍼 (pykrx, FDR 호환)
                def _get_crawled_val(cols):
                    for col in cols:
                        if col in row.index and pd.notna(row[col]):
                            return int(row[col])
                    return 0

                crawled_close = _get_crawled_val(['종가', 'Close'])
                crawled_open = _get_crawled_val(['시가', 'Open'])
                crawled_high = _get_crawled_val(['고가', 'High'])
                crawled_low = _get_crawled_val(['저가', 'Low'])
                
            except Exception as e:
                self._logger.debug(f"크롤링 데이터 파싱 예외({code}): {e} - 스킵")
                continue

            # 3. 시/고/저/종 4개 값 모두 대조 (단 하나라도 다르면 실패)
            if (api_close != crawled_close or 
                api_open != crawled_open or 
                api_high != crawled_high or 
                api_low != crawled_low):
                
                self._logger.warning(
                    f"데이터 불일치 감지! ({code})\n"
                    f" - API   : 시({api_open}) 고({api_high}) 저({api_low}) 종({api_close})\n"
                    f" - {source_name.ljust(6)}: 시({crawled_open}) 고({crawled_high}) 저({crawled_low}) 종({crawled_close})"
                )
                mismatch_count += 1
            else:
                match_count += 1

        if match_count == 0:
            self._logger.warning(f"[{source_name}] 검증 가능한 종목이 없어 실패 처리합니다.")
            return False
            
        # 2개 이상 불일치 시에만 Fallback (단일 종목 거래 정지 등 예외 대응)
        if mismatch_count >= 2:
            self._logger.warning(f"[{source_name}] 데이터 불일치 종목 다수({mismatch_count}개) 발생. 검증 실패.")
            return False

        self._logger.info(f"[{source_name}] 데이터 정합성 검증 통과 (일치: {match_count}, 불일치: {mismatch_count})")
        return True
    
    # ── 3. 수집 티어 구현 ─────────────────────────────────────────
    async def _try_collect_via_fdr(self, target_date: str, start_time: float) -> bool:
        """[Tier 2] FinanceDataReader를 활용한 수집 (차선책)"""
        self._progress["status"] = "FinanceDataReader 일괄 수집 중..."
        
        def _fetch_fdr_sync():
            # FDR은 당일 시세(OHLCV) 전체 리스트를 가져오는 기능을 지원
            df_fdr = fdr.StockListing('KRX')
            if df_fdr.empty:
                raise ValueError("FDR 데이터가 비어있습니다.")
            
            # FDR의 경우 'Close' 컬럼을 '종가'로 맞추어 _verify_crawler_data와 호환되게 함
            df_fdr.rename(columns={'Code': '종목코드', 'Close': '종가'}, inplace=True)
            return df_fdr

        try:
            df_fdr = await asyncio.to_thread(_fetch_fdr_sync)
            
            if not await self._verify_crawler_data(df_fdr, "FDR"):
                return False
                
            formatted_records = self._format_dataframe_to_records(df_fdr)
            await self._save_bulk_to_db_with_progress(target_date, formatted_records, start_time)
            return True
        except Exception as e:
            self._logger.warning(f"FDR 수집 중 예외 발생 (Fallback 시도): {e}")
            return False

    async def _collect_via_broker_api(self, target_date: str, start_time: float) -> None:
        """[Tier 2] 증권사 API 청크 기반 수집 로직 (약 10분 소요)"""
        all_stocks = getattr(self, "_all_stocks_cache", None) or self._load_all_stocks()
        total = len(all_stocks)
        
        # 메인 오케스트레이터에서 넘겨받은 progress 정보를 API 수집 모드에 맞게 갱신
        self._progress["total"] = total
        self._progress["status"] = "증권사 API 수집 중 (Fallback)..."
        
        collected_records: List[Dict] = []
        db_upsert_buffer: List[Dict] = []
        processed = 0

        for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
            # 일시정지(suspend) 체크
            await self._suspend_event.wait()
            
            # 1. 8개씩 병렬 API 호출
            tasks = [self._fetch_with_retry(code) for code, _, _ in chunk]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            # 2. 응답 데이터 추출 및 버퍼에 담기
            batch_records = []
            for (code, name, market), resp in zip(chunk, responses):
                if isinstance(resp, Exception):
                    continue
                record = self._extract_broker_api_record(code, name, market, resp)
                if record:
                    batch_records.append(record)

            if batch_records:
                db_upsert_buffer.extend(batch_records)
                collected_records.extend(batch_records)

            # 3. DB 배치 저장 (500개 도달 시)
            if len(db_upsert_buffer) >= self.DB_UPSERT_BATCH_SIZE:
                await self._stock_repo.upsert_daily_snapshot(target_date, db_upsert_buffer)
                db_upsert_buffer.clear()

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 4. ★ 핵심: 매 청크(8개)마다 진행률 즉시 업데이트
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            processed += len(chunk)
            elapsed = time.time() - start_time
            self._progress.update({
                "processed": processed,
                "collected": len(collected_records),
                "elapsed": round(elapsed, 1)
            })

            # 서버 로그용 출력 (50개 단위 또는 마지막)
            if processed % 50 == 0 or processed >= total:
                self._logger.info(
                    f"[Broker API] 진행: {processed}/{total} "
                    f"({processed / total * 100:.1f}%) "
                    f"| 수집: {len(collected_records)} | 소요: {elapsed:.1f}s"
                )

            # API Rate Limit 회피용 Sleep (1.1초)
            if not all(getattr(r, '_cache_hit', False) for r in responses if not isinstance(r, Exception)):
                await asyncio.sleep(self.CHUNK_SLEEP_SEC)

        # 루프 종료 후 남은 버퍼 최종 저장
        if db_upsert_buffer:
            await self._stock_repo.upsert_daily_snapshot(target_date, db_upsert_buffer)

    # ── 4. 완료 처리 헬퍼 ─────────────────────────────────────────

    async def _finish_collection(self, target_date: str, start_time: float, source: str) -> None:
        """수집 완료 후 공통 후처리 로직"""
        self._last_collected_date = target_date
        elapsed = time.time() - start_time
        self._logger.info(f"전체 종목 수집 완료 (Source: {source}), 소요: {elapsed:.1f}s")
        if self._ns:
            await self._ns.emit(
                NotificationCategory.BACKGROUND, NotificationLevel.INFO, 
                "전체 종목 현재가 수집 완료",
                f"소스: {source} / 소요: {elapsed:.1f}초"
            )

    # # ── 내부 헬퍼 ─────────────────────────────────────────

    async def _fetch_with_retry(self, code: str):
        """get_current_price API 호출 + 재시도."""
        max_retries = 3
        delay = 1.0
        for attempt in range(max_retries):
            try:
                resp = await self._stock_query_service.get_current_price(code, count_stats=False, caller="DailyPriceCollectorTask")
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
    def _extract_broker_api_record(
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
                    f_val = float(val) if val else default
                    # NaN(f_val != f_val) 및 무한대(Inf) 값 방어
                    if f_val != f_val or f_val == float('inf') or f_val == float('-inf'):
                        return default
                    return f_val
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
        """StockCodeRepository에서 KOSPI/KOSDAQ 전체 종목 로드 (ETF/우선주 제외).

        iterrows() 대신 벡터화 마스킹을 사용하여 수십 배 빠르게 필터링한다.
        """
        df = self.stock_code_repository.df
        codes = df["종목코드"].astype(str)
        names = df["종목명"].astype(str)

        mask = (
            codes.ne("")
            & df["시장구분"].isin(("KOSPI", "KOSDAQ"))
            & (codes.str[-1] == "0")
            & ~names.str.startswith(_ETF_PREFIXES)
            & ~names.str.contains("스팩", na=False)
        )
        filtered = df[mask]
        return list(zip(filtered["종목코드"], filtered["종목명"], filtered["시장구분"]))

    def get_progress(self) -> Dict:
        """수집 진행률 반환."""
        return dict(self._progress)

    async def force_collect(self) -> None:
        """강제 수집: FDR 크롤링을 우회하고 증권사 API를 직접 호출하여 w52_high 포함 전 종목 현재가를 수집한다."""
        self._logger.info("DailyPriceCollectorTask 강제 수집 요청 (증권사 API 직접 호출)")
        async with self._running_state():
            if self._is_collecting:
                self._logger.info("현재가 수집 이미 진행 중 — 강제 수집 스킵")
                return

            target_date = await self._mcs.get_latest_trading_date() if self._mcs else None
            if not target_date:
                self._logger.error("최근 거래일을 확인할 수 없어 강제 수집을 중단합니다.")
                return

            self._is_collecting = True
            start_time = time.time()
            self._all_stocks_cache = self._load_all_stocks()

            try:
                self._logger.info(f"전체 종목 강제 수집 시작 (증권사 API, 기준일: {target_date})")
                await self._collect_via_broker_api(target_date, start_time)
                await self._finish_collection(target_date, start_time, "Broker API (Forced)")
            except Exception as e:
                self._logger.error(f"강제 수집 실패: {e}", exc_info=True)
            finally:
                self._is_collecting = False
                self._all_stocks_cache = None

    def _format_dataframe_to_records(self, df: pd.DataFrame) -> List[Dict]:
        """
        pykrx 또는 FinanceDataReader에서 수집한 DataFrame을
        기존 _extract_broker_api_record와 동일한 형태의 DB 레코드 딕셔너리 리스트로 변환한다.
        """
        records = []
        if df is None or df.empty:
            return records

        # 1. DB 기준 종목 메타데이터 (이름, 시장구분) 맵핑용 테이블 생성
        # pykrx의 경우 종목명이나 시장구분 컬럼이 누락되어 있을 수 있으므로 자체 DB 기준으로 매핑합니다.
        # 또한, 이 딕셔너리에 없는 종목(ETF, 스팩, 우선주 등)은 자연스럽게 필터링됩니다.
        all_stocks = getattr(self, "_all_stocks_cache", None) or self._load_all_stocks()
        stock_meta = {
            code: {"name": name, "market": market} 
            for code, name, market in all_stocks
        }

        # 2. 컬럼명 유연성 확보를 위한 헬퍼 함수
        def _get_val(row, possible_cols, default_val):
            for col in possible_cols:
                if col in row.index and pd.notna(row[col]):
                    return row[col]
            return default_val

        # 3. DataFrame 순회 및 레코드 추출
        for _, row in df.iterrows():
            # 종목코드 추출 (문자열 변환 및 6자리 패딩 보장)
            raw_code = _get_val(row, ['종목코드', 'Code'], "")
            code = str(raw_code).zfill(6)
            
            if not code or code == "000000":
                continue

            # 자체 DB 필터링을 통과한 종목만 수집
            meta = stock_meta.get(code)
            if not meta:
                continue

            try:
                # 숫자형 데이터 안전 추출 (pykrx와 FDR의 컬럼명 모두 지원)
                current_price = int(_get_val(row, ['종가', 'Close'], 0))
                open_price = int(_get_val(row, ['시가', 'Open'], 0))
                high_price = int(_get_val(row, ['고가', 'High'], 0))
                low_price = int(_get_val(row, ['저가', 'Low'], 0))
                volume = int(_get_val(row, ['거래량', 'Volume'], 0))
                trading_value = int(_get_val(row, ['거래대금', 'Amount'], 0))
                market_cap = int(_get_val(row, ['시가총액', 'Marcap'], 0))

                per = float(_get_val(row, ['PER'], 0.0))
                pbr = float(_get_val(row, ['PBR'], 0.0))
                eps = float(_get_val(row, ['EPS'], 0.0))

                # 등락 데이터 추정 및 계산
                change_price = int(_get_val(row, ['대비', 'Changes'], 0))
                raw_change_rate = _get_val(row, ['등락률', 'ChagesRatio', 'ChangeRatio'], 0.0)
                change_rate = str(round(float(raw_change_rate), 2))
                
                # 전일 종가 계산 (현재가 - 대비)
                prev_close = current_price - change_price

                # 등락 부호 결정 (API 호환성: 2=상승, 3=보합, 5=하락)
                if change_price > 0:
                    change_sign = "2"
                elif change_price < 0:
                    change_sign = "5"
                else:
                    change_sign = "3"

                record = {
                    "code": code,
                    "name": meta["name"],
                    "current_price": current_price,
                    "open_price": open_price,
                    "high_price": high_price,
                    "low_price": low_price,
                    "prev_close": prev_close,
                    "change_price": change_price,
                    "change_sign": change_sign,
                    "change_rate": change_rate,
                    "volume": volume,
                    "trading_value": trading_value,
                    "market_cap": market_cap,
                    "per": per,
                    "pbr": pbr,
                    "eps": eps,
                    # 일괄 수집 데이터에서는 52주 신고/신저가를 즉각 구하기 어려우므로 None 처리
                    # (DB Upsert 로직에서 NULL(None)이면 기존 값을 유지하도록 설계됨)
                    "w52_high": None,
                    "w52_low": None,
                    "market": meta["market"],
                }
                records.append(record)
                
            except (ValueError, TypeError) as e:
                self._logger.debug(f"데이터 파싱 오류 (종목: {code}): {e}")
                continue

        return records
    
    async def _save_bulk_to_db_with_progress(self, target_date: str, records: List[Dict], start_time: float) -> None:
        """크롤링된 전체 레코드를 DB에 Batch 단위로 저장하며 진행률을 업데이트한다."""
        total_records = len(records)
        if total_records == 0:
            return

        # 필터링 후의 실제 유효 레코드 수로 전체 모수(total) 보정
        self._progress["total"] = total_records
        processed = 0

        for i in range(0, total_records, self.DB_UPSERT_BATCH_SIZE):
            batch = records[i:i + self.DB_UPSERT_BATCH_SIZE]
            
            # DB 저장
            await self._stock_repo.upsert_daily_snapshot(target_date, batch)
            
            # 진행률 업데이트
            processed += len(batch)
            elapsed = time.time() - start_time
            self._progress.update({
                "processed": processed,
                "collected": processed,
                "elapsed": round(elapsed, 1),
                "status": "DB 저장 중..."
            })
            
            # 다른 비동기 태스크(API 응답 등)가 블로킹되지 않도록 제어권 양보
            await asyncio.sleep(0.01)