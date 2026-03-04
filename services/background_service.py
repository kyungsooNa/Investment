# services/background_service.py
"""
백그라운드 배치 작업 전담 서비스.
전체 종목 순회가 필요한 랭킹 집계(외국인/기관 순매수 등)를 담당한다.
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

        # 외국인 순매수 랭킹 캐시
        self._foreign_net_buy_cache: List[Dict] = []
        self._foreign_net_sell_cache: List[Dict] = []
        self._foreign_ranking_updated_at: Optional[datetime] = None
        self._is_refreshing: bool = False

    # ── 외국인 순매수/순매도 랭킹 ────────────────────────────

    async def refresh_foreign_ranking(self) -> None:
        """전체 종목을 순회하여 외국인 순매수/순매도 랭킹을 갱신한다."""
        if self._is_refreshing:
            self._logger.info("외국인 랭킹 갱신 이미 진행 중 — 스킵")
            return

        self._is_refreshing = True
        start_time = time.time()
        self._logger.info("외국인 랭킹 백그라운드 갱신 시작")

        try:
            # 1. 전체 종목 로드
            all_stocks = self._load_all_stocks()
            total = len(all_stocks)
            self._logger.info(f"외국인 랭킹: 전체 {total}개 종목 순회 시작")

            # 2. 종목별 외국계 순매수추이 조회
            results: List[Dict] = []
            processed = 0

            for chunk in _chunked(all_stocks, self.API_CHUNK_SIZE):
                tasks = [
                    self._broker.get_foreign_trading_trend(code)
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

                    glob_ntby_qty = int(resp.data.get("glob_ntby_qty", "0") or "0")
                    results.append({
                        "stck_shrn_iscd": code,
                        "hts_kor_isnm": name,
                        "stck_prpr": resp.data.get("stck_prpr", "0"),
                        "prdy_ctrt": resp.data.get("prdy_ctrt", "0"),
                        "prdy_vrss": resp.data.get("prdy_vrss", "0"),
                        "prdy_vrss_sign": resp.data.get("prdy_vrss_sign", ""),
                        "acml_vol": resp.data.get("acml_vol", "0"),
                        "glob_ntby_qty": str(glob_ntby_qty),
                        "frgn_ntby_qty_icdc": resp.data.get("frgn_ntby_qty_icdc", "0"),
                    })

                processed += len(chunk)
                if processed % 50 == 0 or processed >= total:
                    elapsed = time.time() - start_time
                    self._logger.info(
                        f"외국인 랭킹 진행: {processed}/{total} ({processed/total*100:.1f}%) "
                        f"| 수집: {len(results)} | 소요: {elapsed:.1f}s"
                    )

                await asyncio.sleep(self.CHUNK_SLEEP_SEC)

            # 3. 정렬: 순매수 상위 30 / 순매도 하위 30
            results.sort(key=lambda x: int(x["glob_ntby_qty"]), reverse=True)

            net_buy_top = [dict(item) for item in results[:30]]
            for i, item in enumerate(net_buy_top, 1):
                item["data_rank"] = str(i)

            sell_slice = results[-30:] if len(results) >= 30 else results[:]
            net_sell_top = [dict(item) for item in reversed(sell_slice)]
            for i, item in enumerate(net_sell_top, 1):
                item["data_rank"] = str(i)

            self._foreign_net_buy_cache = net_buy_top
            self._foreign_net_sell_cache = net_sell_top
            self._foreign_ranking_updated_at = datetime.now()

            elapsed = time.time() - start_time
            self._logger.info(
                f"외국인 랭킹 갱신 완료: 순매수 {len(net_buy_top)}개, "
                f"순매도 {len(net_sell_top)}개, 소요: {elapsed:.1f}s"
            )
        except Exception as e:
            self._logger.error(f"외국인 랭킹 갱신 실패: {e}", exc_info=True)
        finally:
            self._is_refreshing = False

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
                loop = asyncio.get_running_loop()
                self._logger.info("외국인 랭킹 캐시 없음 → 온디맨드 백그라운드 갱신 트리거")
                asyncio.create_task(self.refresh_foreign_ranking())
            except RuntimeError:
                self._logger.warning("이벤트 루프 없음 — 온디맨드 갱신 스킵")
        return None

    def get_foreign_net_buy_ranking(self, limit: int = 30) -> ResCommonResponse:
        """외국인 순매수 상위 랭킹 반환 (캐시에서 즉시)."""
        blocked = self._check_and_trigger_refresh()
        if blocked:
            return blocked
        if not self._foreign_net_buy_cache:
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="데이터 수집 중...",
                data=[]
            )
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="외국인 순매수 상위 종목 조회 성공",
            data=self._foreign_net_buy_cache[:limit]
        )

    def get_foreign_net_sell_ranking(self, limit: int = 30) -> ResCommonResponse:
        """외국인 순매도 상위 랭킹 반환 (캐시에서 즉시)."""
        blocked = self._check_and_trigger_refresh()
        if blocked:
            return blocked
        if not self._foreign_net_sell_cache:
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="데이터 수집 중...",
                data=[]
            )
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="외국인 순매도 상위 종목 조회 성공",
            data=self._foreign_net_sell_cache[:limit]
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────

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
