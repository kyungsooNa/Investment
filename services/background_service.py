# services/background_service.py
"""
백그라운드 배치 작업 전담 서비스.
전체 종목 순회가 필요한 랭킹 집계(외국인/기관/개인 순매수 등)와
장마감 후 기본 랭킹 캐시를 담당한다.
"""
import asyncio
import logging
import time
from datetime import datetime
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from brokers.broker_api_wrapper import BrokerAPIWrapper
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from common.types import ResCommonResponse, ErrorCode
from core.time_manager import TimeManager
from market_data.stock_code_mapper import StockCodeMapper
from core.performance_manager import PerformanceManager
from managers.telegram_notifier import TelegramReporter
from managers.notification_manager import NotificationManager


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


# ETF/ETN 브랜드명 접두사 (TradingService._ETF_PREFIXES 와 동일)
_ETF_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
    "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
    "VITA", "TREX", "MASTER", "WOORI", "KINDEX",
)


class BackgroundService:
    """백그라운드 배치 작업을 관리하는 서비스."""

    # 청크 크기 및 레이트 리밋
    API_CHUNK_SIZE = 8
    CHUNK_SLEEP_SEC = 1.1

    # 장마감 후 대기 시간 (초) — 15:30 이후 약간의 여유
    AFTER_MARKET_DELAY_SEC = 60

    def __init__(
        self,
        broker_api_wrapper: BrokerAPIWrapper,
        stock_code_mapper: StockCodeMapper,
        env: KoreaInvestApiEnv = None,
        logger=None,
        time_manager: TimeManager = None,
        trading_service=None,
        performance_manager: Optional[PerformanceManager] = None,
        notification_manager: Optional[NotificationManager] = None,
        telegram_reporter: Optional[TelegramReporter] = None,
    ):
        self._broker = broker_api_wrapper
        self._mapper = stock_code_mapper
        self._env = env
        self._logger = logger or logging.getLogger(__name__)
        self._time_manager = time_manager
        self._trading_service = trading_service
        self.pm = performance_manager if performance_manager else PerformanceManager(enabled=False)
        self._nm = notification_manager
        self._telegram_reporter = telegram_reporter

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
        self._investor_ranking_updated_at: Optional[datetime] = None
        self._is_refreshing: bool = False

        # 기본 랭킹 캐시 (상승/하락/거래량/거래대금) — 장마감 후 1회
        self._basic_ranking_cache: Dict[str, ResCommonResponse] = {}
        self._basic_ranking_updated_at: Optional[datetime] = None

        # 장마감 후 자동 갱신 태스크
        self._after_market_task: Optional[asyncio.Task] = None

        # 진행률 상태
        self._progress: Dict = {
            "running": False,
            "processed": 0,
            "total": 0,
            "collected": 0,
            "elapsed": 0.0,
        }

    # ── 장마감 후 자동 갱신 스케줄러 ────────────────────────────

    async def start_after_market_scheduler(self) -> None:
        """장마감 후 자동으로 랭킹 갱신을 스케줄링하는 루프."""
        self._logger.info("장마감 후 자동 갱신 스케줄러 시작")
        while True:
            try:
                if self._time_manager and not self._time_manager.is_market_open():
                    # 장 마감 상태 — 오늘 갱신한 적 없으면 갱신
                    today = datetime.now().strftime("%Y%m%d")
                    needs_investor = (
                        not self._investor_ranking_updated_at
                        or self._investor_ranking_updated_at.strftime("%Y%m%d") != today
                    )
                    needs_basic = (
                        not self._basic_ranking_updated_at
                        or self._basic_ranking_updated_at.strftime("%Y%m%d") != today
                    )

                    if needs_basic:
                        await self.refresh_basic_ranking()
                    if needs_investor:
                        await self.refresh_investor_ranking()

                await asyncio.sleep(300)  # 5분마다 체크
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"장마감 후 스케줄러 오류: {e}", exc_info=True)
                await asyncio.sleep(60)

    # ── 기본 랭킹 캐시 (상승/하락/거래량/거래대금) ───────────────

    async def refresh_basic_ranking(self) -> None:
        """상승률/하락률/거래량/거래대금 랭킹을 1회 조회하여 캐시."""
        if not self._trading_service:
            self._logger.warning("TradingService 미설정 — 기본 랭킹 캐시 스킵")
            return

        t_start = self.pm.start_timer()
        self._logger.info("기본 랭킹 캐시 갱신 시작 (상승/하락/거래량/거래대금)")
        try:
            rise_resp, fall_resp, vol_resp, tv_resp = await asyncio.gather(
                self._trading_service.get_top_rise_fall_stocks(True),
                self._trading_service.get_top_rise_fall_stocks(False),
                self._trading_service.get_top_volume_stocks(),
                self._trading_service.get_top_trading_value_stocks(),
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
            self.pm.log_timer("BackgroundService.refresh_basic_ranking", t_start, threshold=1.0)
            if self._nm:
                await self._nm.emit(
                    "API", "info", "기본 랭킹 갱신 완료",
                    f"상승/하락/거래량/거래대금 캐시 갱신 완료",
                )
        except Exception as e:
            self._logger.error(f"기본 랭킹 캐시 갱신 실패: {e}", exc_info=True)
            if self._nm:
                await self._nm.emit("SYSTEM", "error", "기본 랭킹 갱신 실패", str(e))

    def get_investor_ranking_progress(self) -> Dict:
        """투자자 랭킹 수집 진행률 반환."""
        return dict(self._progress)

    def get_basic_ranking_cache(self, category: str) -> Optional[ResCommonResponse]:
        """장마감 후 캐시된 기본 랭킹 반환. 캐시 없으면 None."""
        return self._basic_ranking_cache.get(category)

    # ── 투자자별 순매수/순매도 랭킹 ────────────────────────────

    async def _fetch_with_retry(self, api_call, *args, **kwargs):
        """API 호출을 재시도 로직으로 감싸는 헬퍼."""
        max_retries = 3
        delay = 1.0  # 초
        for attempt in range(max_retries):
            try:
                resp = await api_call(*args, **kwargs)
                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
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
        return None  # 최종 실패 시 None 반환

    async def refresh_investor_ranking(self) -> None:
        """전체 종목을 순회하여 외국인/기관/개인 순매수/순매도 랭킹을 갱신한다."""
        # [성능 보호] 장 중에는 실행하지 않음
        if self._time_manager and self._time_manager.is_market_open():
            self._logger.info("장 운영 중이므로 투자자 랭킹 전체 갱신을 건너뜁니다.")
            return

        if self._is_refreshing:
            self._logger.info("투자자 랭킹 갱신 이미 진행 중 — 스킵")
            return

        t_start_total = self.pm.start_timer()
        self._is_refreshing = True
        start_time = time.time()
        today = datetime.now().strftime("%Y%m%d")
        self._logger.info("투자자 랭킹 백그라운드 갱신 시작")
        
        # [변경] 오늘 날짜 대신 실제 장이 열린 최근 날짜 조회
        target_date = None
        if self._trading_service:
            target_date = await self._trading_service.get_latest_trading_date()

        if not target_date:
            self._logger.error("최근 거래일을 확인할 수 없어 투자자 랭킹 갱신을 중단합니다.")
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
                        # _fetch_with_retry에서 이미 로깅했으므로 여기서는 스킵
                        continue
                    if not resp:  # 최종 실패 시 None이 반환됨
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
                        # _fetch_with_retry에서 이미 로깅했으므로 여기서는 스킵
                        continue
                    if not resp:  # 최종 실패 시 None이 반환됨
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

                # 전체 캐시 HIT면 sleep 불필요, 실제 API 호출이 있었으면 rate limit sleep
                all_cache_hit = all(
                    getattr(r, '_cache_hit', False)
                    for r in all_responses if not isinstance(r, Exception)
                )
                if not all_cache_hit:
                    await asyncio.sleep(self.CHUNK_SLEEP_SEC)

            # 2-1. 프로그램 데이터의 acml_tr_pbmn으로 투자자 결과 보정
            #      (투자자 API output1에 acml_tr_pbmn이 없거나 "0"일 수 있음)
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

            elapsed = time.time() - start_time
            self._logger.info(
                f"투자자 랭킹 갱신 완료: {len(results)}개 종목 수집, 소요: {elapsed:.1f}s"
            )
            self.pm.log_timer("BackgroundService.refresh_investor_ranking", t_start_total, threshold=10.0)
            if self._nm:
                await self._nm.emit(
                    "API", "info", "투자자 랭킹 갱신 완료",
                    f"{len(results)}개 종목 수집, 소요: {elapsed:.1f}초",
                )
            if self._telegram_reporter:
                self._logger.info("텔레그램 랭킹 리포트 전송 시작")
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
                try:
                    await self._telegram_reporter.send_ranking_report(rankings_for_report, report_date=target_date)
                    self._logger.info("텔레그램 랭킹 리포트 전송 완료")
                except Exception as e:
                    self._logger.error(f"텔레그램 랭킹 리포트 전송 중 오류: {e}", exc_info=True)
        except Exception as e:
            self._logger.error(f"투자자 랭킹 갱신 실패: {e}", exc_info=True)
            if self._nm:
                await self._nm.emit("SYSTEM", "error", "투자자 랭킹 갱신 실패", str(e))
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

    def get_trading_value_ranking(self, limit: int = 30) -> ResCommonResponse:
        """투자자 데이터 기반 거래대금 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._trading_value_cache, "거래대금", limit)

    def _check_and_trigger_refresh(self) -> Optional[ResCommonResponse]:
        """캐시 비어있으면 온디맨드 갱신 트리거. 즉시 반환할 응답이 있으면 반환."""
        # [성능 보호] 장 중에는 온디맨드 갱신 트리거 안 함
        if self._time_manager and self._time_manager.is_market_open():
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

    def get_foreign_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """외국인 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._foreign_net_buy_cache, "외국인 순매수", limit)

    def get_foreign_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """외국인 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._foreign_net_sell_cache, "외국인 순매도", limit)

    # ── 기관 ──

    def get_inst_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """기관 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._inst_net_buy_cache, "기관 순매수", limit)

    def get_inst_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """기관 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._inst_net_sell_cache, "기관 순매도", limit)

    # ── 개인 ──

    def get_prsn_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """개인 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._prsn_net_buy_cache, "개인 순매수", limit)

    def get_prsn_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """개인 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._prsn_net_sell_cache, "개인 순매도", limit)

    # ── 프로그램 ──

    def get_program_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """프로그램 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._program_net_buy_cache, "프로그램 순매수", limit)

    def get_program_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """프로그램 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        return self._get_ranking_from_cache(self._program_net_sell_cache, "프로그램 순매도", limit)

    # ── 내부 헬퍼 ─────────────────────────────────────────────

    def _get_ranking_from_cache(self, cache: List[Dict], label: str, limit: int) -> ResCommonResponse:
        """캐시에서 랭킹 데이터 반환. 캐시 없으면 트리거 + 빈 응답."""
        blocked = self._check_and_trigger_refresh()
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
        """StockCodeMapper에서 KOSPI/KOSDAQ 전체 종목 로드."""
        all_stocks = []
        for _, row in self._mapper.df.iterrows():
            code = row.get("종목코드", "")
            name = row.get("종목명", "")
            market = row.get("시장구분", "")

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
