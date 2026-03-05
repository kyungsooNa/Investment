# services/background_service.py
"""
백그라운드 배치 작업 전담 서비스.
전체 종목 순회가 필요한 랭킹 집계(외국인/기관/개인 순매수 등)를 담당한다.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional

from brokers.broker_api_wrapper import BrokerAPIWrapper
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from common.types import ResCommonResponse, ErrorCode
from core.time_manager import TimeManager
from market_data.stock_code_mapper import StockCodeMapper


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
    API_CHUNK_SIZE = 5
    CHUNK_SLEEP_SEC = 1.1

    def __init__(
        self,
        broker_api_wrapper: BrokerAPIWrapper,
        stock_code_mapper: StockCodeMapper,
        env: KoreaInvestApiEnv = None,
        logger=None,
        time_manager: TimeManager = None,
    ):
        self._broker = broker_api_wrapper
        self._mapper = stock_code_mapper
        self._env = env
        self._logger = logger or logging.getLogger(__name__)
        self._time_manager = time_manager

        # 투자자별 순매수 랭킹 캐시
        self._foreign_net_buy_cache: List[Dict] = []
        self._foreign_net_sell_cache: List[Dict] = []
        self._inst_net_buy_cache: List[Dict] = []
        self._inst_net_sell_cache: List[Dict] = []
        self._prsn_net_buy_cache: List[Dict] = []
        self._prsn_net_sell_cache: List[Dict] = []
        self._investor_ranking_updated_at: Optional[datetime] = None
        self._is_refreshing: bool = False

    # ── 투자자별 순매수/순매도 랭킹 ────────────────────────────

    async def refresh_investor_ranking(self) -> None:
        """전체 종목을 순회하여 외국인/기관/개인 순매수/순매도 랭킹을 갱신한다."""
        if self._is_refreshing:
            self._logger.info("투자자 랭킹 갱신 이미 진행 중 — 스킵")
            return

        self._is_refreshing = True
        start_time = time.time()
        today = datetime.now().strftime("%Y%m%d")
        self._logger.info("투자자 랭킹 백그라운드 갱신 시작")

        try:
            # 1. 전체 종목 로드
            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            self._logger.info(f"투자자 랭킹: 전체 {total}개 종목 순회 시작")

            # 2. 종목별 투자자 매매동향 조회
            results: List[Dict] = []
            processed = 0

            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                tasks = [
                    self._broker.get_investor_trade_by_stock_daily(code, today)
                    for code, _, _ in chunk
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                for (code, name, market), resp in zip(chunk, responses):
                    if isinstance(resp, Exception):
                        continue
                    if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                        continue
                    if not resp.data or not isinstance(resp.data, dict):
                        continue

                    # ETF/ETN 제외
                    if any(name.startswith(p) for p in _ETF_PREFIXES):
                        continue

                    frgn_qty = int(resp.data.get("frgn_ntby_qty", "0") or "0")
                    orgn_qty = int(resp.data.get("orgn_ntby_qty", "0") or "0")
                    prsn_qty = int(resp.data.get("prsn_ntby_qty", "0") or "0")

                    results.append({
                        "stck_shrn_iscd": code,
                        "hts_kor_isnm": name,
                        "stck_prpr": resp.data.get("stck_prpr", "0"),
                        "prdy_ctrt": resp.data.get("prdy_ctrt", "0"),
                        "prdy_vrss": resp.data.get("prdy_vrss", "0"),
                        "prdy_vrss_sign": resp.data.get("prdy_vrss_sign", ""),
                        "acml_vol": resp.data.get("acml_vol", "0"),
                        "frgn_ntby_qty": str(frgn_qty),
                        "orgn_ntby_qty": str(orgn_qty),
                        "prsn_ntby_qty": str(prsn_qty),
                    })

                processed += len(chunk)
                if processed % 50 == 0 or processed >= total:
                    elapsed = time.time() - start_time
                    self._logger.info(
                        f"투자자 랭킹 진행: {processed}/{total} ({processed/total*100:.1f}%) "
                        f"| 수집: {len(results)} | 소요: {elapsed:.1f}s"
                    )

                await asyncio.sleep(self.CHUNK_SLEEP_SEC)

            # 3. 투자자별 정렬 → 상위 30 / 하위 30
            self._foreign_net_buy_cache, self._foreign_net_sell_cache = \
                self._build_ranking(results, "frgn_ntby_qty")
            self._inst_net_buy_cache, self._inst_net_sell_cache = \
                self._build_ranking(results, "orgn_ntby_qty")
            self._prsn_net_buy_cache, self._prsn_net_sell_cache = \
                self._build_ranking(results, "prsn_ntby_qty")
            self._investor_ranking_updated_at = datetime.now()

            elapsed = time.time() - start_time
            self._logger.info(
                f"투자자 랭킹 갱신 완료: {len(results)}개 종목 수집, 소요: {elapsed:.1f}s"
            )
        except Exception as e:
            self._logger.error(f"투자자 랭킹 갱신 실패: {e}", exc_info=True)
        finally:
            self._is_refreshing = False

    @staticmethod
    def _build_ranking(results: List[Dict], qty_field: str, top_n: int = 30):
        """순매수수량 필드 기준 정렬 → (상위 30, 하위 30) 튜플 반환."""
        sorted_list = sorted(results, key=lambda x: int(x[qty_field]), reverse=True)

        buy_top = [dict(item) for item in sorted_list[:top_n]]
        for i, item in enumerate(buy_top, 1):
            item["data_rank"] = str(i)

        sell_slice = sorted_list[-top_n:] if len(sorted_list) >= top_n else sorted_list[:]
        sell_top = [dict(item) for item in reversed(sell_slice)]
        for i, item in enumerate(sell_top, 1):
            item["data_rank"] = str(i)

        return buy_top, sell_top

    def _check_and_trigger_refresh(self) -> Optional[ResCommonResponse]:
        """모의투자 체크 + 캐시 비어있으면 온디맨드 갱신 트리거. 즉시 반환할 응답이 있으면 반환."""
        if self._env and self._env.is_paper_trading:
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="실전투자 전용 기능입니다. 실전투자 모드로 전환 후 이용해주세요.",
                data=[]
            )
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
            if code and market in ("KOSPI", "KOSDAQ"):
                all_stocks.append((code, name, market))
        return all_stocks
