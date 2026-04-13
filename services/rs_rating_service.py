# services/rs_rating_service.py
"""
RS Rating (IBD/오닐 방식 1~99 백분위) 계산 서비스.

오닐 가중 RS 공식:
    Weighted RS = (C0 * 2 + C1 + C2 + C3) / 5
    - C0: 최근 분기(63일) 수익률  ← 2배 가중
    - C1~C3: 이전 각 분기 수익률

전체 종목의 Weighted RS를 한 번에 계산하고 백분위 순위(1~99)로 변환하여 SQLite에 저장합니다.
일일 배치(장 마감 후)로 실행되어 NewHighTask, OneilUniverseService에서 조회에 활용됩니다.
"""
import asyncio
import logging
from typing import Optional, List, Dict, TYPE_CHECKING

import pandas as pd

from common.types import ResCommonResponse, ErrorCode, ResRSRating
from core.performance_profiler import PerformanceProfiler

if TYPE_CHECKING:
    from repositories.stock_ohlcv_repository import StockOhlcvRepository
    from repositories.rs_rating_repository import RSRatingRepository
    from repositories.stock_code_repository import StockCodeRepository

# 기본 청크 크기 (asyncio.gather 과부하 방지)
_CHUNK_SIZE = 50


class RSRatingService:
    """
    IBD/오닐 방식의 RS Rating(1~99)을 계산·저장·조회하는 서비스.
    """

    def __init__(
        self,
        stock_ohlcv_repository: "StockOhlcvRepository",
        rs_rating_repository: "RSRatingRepository",
        stock_code_repository: "StockCodeRepository",
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
    ):
        self._ohlcv_repo = stock_ohlcv_repository
        self._rs_repo = rs_rating_repository
        self._code_repo = stock_code_repository
        self._logger = logger or logging.getLogger(__name__)
        self.pm = performance_profiler or PerformanceProfiler(enabled=False)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    async def compute_and_store_ratings(
        self,
        trade_date: str,
        codes: Optional[List[str]] = None,
    ) -> ResCommonResponse:
        """전체(또는 지정) 종목의 RS Rating을 계산하고 DB에 저장합니다.

        Args:
            trade_date: 계산 기준 날짜 (YYYYMMDD)
            codes: 특정 종목만 계산할 경우 지정. None이면 전체 종목.

        Returns:
            ResCommonResponse[dict]: data={"saved": int, "total": int}
        """
        t_start = self.pm.start_timer()
        try:
            # 1. 대상 종목 코드 목록 결정
            target_codes = codes if codes else list(self._code_repo.code_to_name.keys())
            if not target_codes:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1="계산할 종목 코드가 없습니다.",
                    data=None,
                )

            self._logger.info({
                "event": "rs_rating_compute_start",
                "trade_date": trade_date,
                "total_codes": len(target_codes),
            })

            # 2. 종목별 OHLCV 조회 및 Weighted RS 계산 (청크 병렬 처리)
            weighted_rs_map: Dict[str, float] = {}
            for i in range(0, len(target_codes), _CHUNK_SIZE):
                chunk = target_codes[i: i + _CHUNK_SIZE]
                results = await asyncio.gather(
                    *[self._fetch_weighted_rs(code) for code in chunk],
                    return_exceptions=False,
                )
                for code, w_rs in zip(chunk, results):
                    if w_rs is not None:
                        weighted_rs_map[code] = w_rs

            if not weighted_rs_map:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1="RS 계산 가능한 종목이 없습니다 (OHLCV 데이터 부족).",
                    data=None,
                )

            # 3. 백분위 순위 → 1~99 변환
            rating_map = self._compute_percentile_ratings(weighted_rs_map)

            # 4. DB 배치 저장
            records = [
                {
                    "trade_date": trade_date,
                    "code": code,
                    "rs_rating": rating,
                    "weighted_rs": weighted_rs_map[code],
                }
                for code, rating in rating_map.items()
            ]
            saved = await self._rs_repo.upsert_batch(records)

            self.pm.log_timer(
                f"RSRatingService.compute_and_store_ratings({trade_date})",
                t_start,
                extra_info=f"saved={saved}/{len(target_codes)}",
            )
            self._logger.info({
                "event": "rs_rating_compute_done",
                "trade_date": trade_date,
                "computed": len(rating_map),
                "saved": saved,
            })

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1=f"RS Rating 계산 완료 ({saved}건 저장)",
                data={"saved": saved, "total": len(target_codes)},
            )

        except Exception as e:
            self._logger.exception(f"RSRatingService.compute_and_store_ratings 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=str(e),
                data=None,
            )

    async def get_rating(
        self,
        code: str,
        trade_date: Optional[str] = None,
    ) -> ResCommonResponse:
        """단일 종목 RS Rating 조회.

        trade_date 미지정 시 가장 최근에 계산된 날짜 기준.
        """
        try:
            if trade_date is None:
                trade_date = await self._rs_repo.get_latest_date()
                if trade_date is None:
                    return ResCommonResponse(
                        rt_cd=ErrorCode.EMPTY_VALUES.value,
                        msg1="RS Rating 데이터가 없습니다.",
                        data=None,
                    )

            result = await self._rs_repo.get_single(code, trade_date)
            if result is None:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1=f"{code} / {trade_date} RS Rating 없음",
                    data=None,
                )
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=result,
            )
        except Exception as e:
            self._logger.exception(f"RSRatingService.get_rating({code}) 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=str(e),
                data=None,
            )

    async def get_ratings_by_date(
        self,
        trade_date: str,
    ) -> ResCommonResponse:
        """특정 날짜의 전체 종목 RS Rating 딕셔너리 반환.

        Returns:
            ResCommonResponse[Dict[str, int]]: data={code: rs_rating}
        """
        try:
            rating_map = await self._rs_repo.get_by_date(trade_date)
            if not rating_map:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1=f"{trade_date} RS Rating 데이터 없음",
                    data=None,
                )
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=rating_map,
            )
        except Exception as e:
            self._logger.exception(f"RSRatingService.get_ratings_by_date({trade_date}) 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=str(e),
                data=None,
            )

    async def get_rs_line(
        self,
        code: str,
        benchmark_code: str = "069500",  # 기본값: KODEX 200 (KOSPI 대용)
        limit: int = 90,
    ) -> ResCommonResponse:
        """RS Line 데이터 반환.

        RS Line = (종목 종가 / 벤치마크 종가) × 100  (첫 날 = 100 기준)

        Args:
            code: 대상 종목코드
            benchmark_code: 벤치마크 ETF 종목코드 (기본: KODEX 200 069500)
            limit: 조회 기간 (캔들 수)

        Returns:
            ResCommonResponse[List[dict]]:
                data=[{"date", "close", "rs_line", "rs_line_new_high"}, ...]
        """
        try:
            stock_data = await self._ohlcv_repo.get_stock_data(code, ohlcv_limit=limit)
            bench_data = await self._ohlcv_repo.get_stock_data(benchmark_code, ohlcv_limit=limit)

            if not stock_data or not bench_data:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1="RS Line 계산에 필요한 OHLCV 데이터 없음",
                    data=None,
                )

            stock_ohlcv = stock_data.get("ohlcv", [])
            bench_ohlcv = bench_data.get("ohlcv", [])

            # 날짜 인덱스 정렬 후 inner join
            stock_df = pd.DataFrame(stock_ohlcv)[["date", "close"]].rename(columns={"close": "stock_close"})
            bench_df = pd.DataFrame(bench_ohlcv)[["date", "close"]].rename(columns={"close": "bench_close"})

            merged = stock_df.merge(bench_df, on="date", how="inner")
            if merged.empty:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1="공통 날짜 데이터 없음",
                    data=None,
                )

            merged["stock_close"] = pd.to_numeric(merged["stock_close"], errors="coerce")
            merged["bench_close"] = pd.to_numeric(merged["bench_close"], errors="coerce")
            merged = merged.dropna(subset=["stock_close", "bench_close"])
            merged = merged[merged["bench_close"] > 0]

            # 첫 날 기준 100으로 정규화
            base_ratio = merged.iloc[0]["stock_close"] / merged.iloc[0]["bench_close"]
            merged["rs_line"] = (merged["stock_close"] / merged["bench_close"] / base_ratio * 100).round(4)

            # RS Line 전고점 돌파 감지 (주가 전고점 미돌파 구간에서 RS 신고가 = 강세 신호)
            rs_cummax = merged["rs_line"].cummax().shift(1)
            merged["rs_line_new_high"] = merged["rs_line"] > rs_cummax

            result = [
                {
                    "date": str(row["date"]),
                    "close": int(row["stock_close"]),
                    "rs_line": row["rs_line"],
                    "rs_line_new_high": bool(row["rs_line_new_high"]),
                }
                for _, row in merged.iterrows()
            ]

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=result,
            )

        except Exception as e:
            self._logger.exception(f"RSRatingService.get_rs_line({code}) 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=str(e),
                data=None,
            )

    # ── 내부 계산 메서드 ───────────────────────────────────────────────────────

    async def _fetch_weighted_rs(self, code: str) -> Optional[float]:
        """단일 종목 OHLCV 조회 후 Weighted RS 계산. 데이터 부족 시 None."""
        try:
            data = await self._ohlcv_repo.get_stock_data(code, ohlcv_limit=270)
            if not data:
                return None
            ohlcv = data.get("ohlcv", [])
            return self.calc_weighted_rs(ohlcv)
        except Exception:
            return None

    @staticmethod
    def calc_weighted_rs(ohlcv_data: List[Dict]) -> Optional[float]:
        """오닐 가중 RS 계산 (정적 메서드, 동기).

        공식: (C0 × 2 + C1 + C2 + C3) / 5
            - C0: 최근 63일 수익률 (가중치 2배)
            - C1: 직전 63일(64~126일) 수익률
            - C2: 직전전 63일(127~189일) 수익률
            - C3: 63~252일 전 수익률

        최소 64개 캔들 필요. 부족한 분기는 가중 평균에서 제외 후 재정규화.
        """
        n = len(ohlcv_data)
        if n < 64:
            return None

        def _qret(start_idx: int, end_idx: int) -> Optional[float]:
            """슬라이스 내 수익률 계산. 인덱스는 음수(끝에서 역방향)."""
            try:
                end = ohlcv_data[end_idx]
                start = ohlcv_data[start_idx]
                past = float(start.get("close", 0) or 0)
                recent = float(end.get("close", 0) or 0)
                if past <= 0:
                    return None
                return (recent - past) / past * 100
            except (IndexError, TypeError, ValueError):
                return None

        # 최근 기준 — 음수 인덱싱으로 슬라이스 경계 지정
        # C0: ohlcv[-64] → ohlcv[-1]  (63-day return)
        c0 = _qret(-64, -1)
        # C1: ohlcv[-127] → ohlcv[-64]
        c1 = _qret(-127, -64) if n >= 127 else None
        # C2: ohlcv[-190] → ohlcv[-127]
        c2 = _qret(-190, -127) if n >= 190 else None
        # C3: ohlcv[-253] → ohlcv[-190]
        c3 = _qret(-253, -190) if n >= 253 else None

        if c0 is None:
            return None

        # 가용 분기만으로 정규화 (가중치 합 = 2+1+1+1 = 5 기준)
        total_weight = 2.0
        weighted_sum = c0 * 2.0

        for c, w in [(c1, 1.0), (c2, 1.0), (c3, 1.0)]:
            if c is not None:
                weighted_sum += c * w
                total_weight += w

        return weighted_sum / total_weight

    @staticmethod
    def _compute_percentile_ratings(
        weighted_rs_map: Dict[str, float],
    ) -> Dict[str, int]:
        """Weighted RS 딕셔너리를 1~99 백분위 순위로 변환.

        pandas rank(pct=True) 사용 → [0, 1] 범위 → 1~99 스케일.
        동점(ties)은 'average' 방식으로 처리.
        """
        if not weighted_rs_map:
            return {}

        series = pd.Series(weighted_rs_map)
        # pct=True: 0.0 ~ 1.0 범위 (최솟값=가장 작은 분위)
        pct = series.rank(pct=True, method="average")
        # 1~99 변환 후 정수 클리핑
        ratings = (pct * 99).round().clip(1, 99).astype(int)
        return ratings.to_dict()
