# strategies/oneil/universe_service.py
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from common.types import ErrorCode
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from repositories.stock_code_repository import StockCodeRepository
from services.naver_finance_scraper_service import NaverFinanceScraperService
from core.market_clock import MarketClock
from strategies.oneil_common_types import OneilUniverseConfig, OSBWatchlistItem
from core.logger import get_strategy_logger
from core.performance_profiler import PerformanceProfiler
from services.price_subscription_service import SubscriptionPriority
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from services.rs_rating_service import RSRatingService
    from services.minervini_stage_service import MinerviniStageService

def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


class OneilUniverseService:
    """오닐 전략 유니버스 관리 서비스.
    
    역할:
      1. 전일 기준 우량주 생성 및 로드 (Pool A)
      2. 당일 급등주 실시간 발굴 (Pool B)
      3. Watchlist (감시 대상 60종목) 병합 및 제공
      4. 마켓 타이밍 판단
    """

    def __init__(
        self,
        stock_query_service: StockQueryService,
        indicator_service: IndicatorService,
        stock_code_repository: StockCodeRepository,
        market_clock: MarketClock,
        scraper_service: Optional[NaverFinanceScraperService] = None,  # 추가됨
        config: Optional[OneilUniverseConfig] = None,
        logger: Optional[logging.Logger] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        price_subscription_service=None,
        rs_rating_service: Optional["RSRatingService"] = None,
        minervini_service: Optional["MinerviniStageService"] = None,
        notification_service: Optional[NotificationService] = None,
    ):
        self._sqs = stock_query_service
        self._indicator = indicator_service
        self.stock_code_repository = stock_code_repository
        self._tm = market_clock
        self._scraper = scraper_service
        self._cfg = config or OneilUniverseConfig()
        self._logger = logger or logging.getLogger(__name__)
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._price_sub_svc = price_subscription_service
        self._rs_rating_service = rs_rating_service
        self._minervini_svc = minervini_service
        self._notification_service = notification_service

        # 상태 관리
        self._watchlist: Dict[str, OSBWatchlistItem] = {}
        self._watchlist_date: str = ""
        self._watchlist_refresh_done: set = set()
        self._pool_a_loaded: bool = False
        self._pool_a_items: Dict[str, OSBWatchlistItem] = {}
        
        # 마켓 타이밍 캐시
        self._market_timing_cache: Dict[str, bool] = {}
        self._market_timing_date: str = ""

        # 전일 기준 우량주 생성 진행률
        self._generation_progress: Dict = {
            "running": False,
            "phase": None,
            "processed": 0,
            "total": 0,
            "passed": 0,
            "selected": 0,
            "elapsed": 0.0,
        }

    @property
    def generation_progress(self) -> Dict:
        """전일 기준 우량주 생성 진행률 스냅샷 반환."""
        return dict(self._generation_progress)

    async def get_watchlist(self, logger: Optional[logging.Logger] = None) -> Dict[str, OSBWatchlistItem]:
        """현재 유효한 워치리스트를 반환 (캐싱 + 자동 갱신)."""
        logger = logger or self._logger
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        
        # 날짜 변경 시 초기화
        if self._watchlist_date != today:
            self._watchlist_refresh_done = set()
            self._pool_a_loaded = False
            self._pool_a_items = {}
            await self._build_watchlist(logger=logger)
            self._watchlist_date = today
            self._market_timing_date = "" # 마켓타이밍도 재확인 필요
            
            # 초기화 시점에도 현재 시간 기준 이미 지난 갱신 주기는 완료 처리하여 중복 갱신 방지
            self._should_refresh_watchlist()
        
        # 장중 갱신 주기 체크
        elif self._should_refresh_watchlist():
            await self._build_watchlist(logger=logger)

        if self._price_sub_svc and self._watchlist:
            asyncio.create_task(self._price_sub_svc.sync_subscriptions(
                codes=list(self._watchlist.keys()),
                category_key="strategy_oneil",
                priority=SubscriptionPriority.MEDIUM,
            ))
        return self._watchlist

    async def is_market_timing_ok(self, market: str, caller: str = "", logger: Optional[logging.Logger] = None) -> bool:
        """해당 시장(KOSPI/KOSDAQ)의 마켓 타이밍이 매수 적합한지 확인."""
        logger = logger or self._logger
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if self._market_timing_date != today:
            await self._update_market_timing(caller=caller, logger=logger)
            self._market_timing_date = today
        
        return self._market_timing_cache.get(market, False)

    # ── 워치리스트 빌드 ────────────────────────────────────────────

    async def _build_watchlist(self, logger: Optional[logging.Logger] = None):
        """Pool A + Pool B 병합 -> 스코어링 -> 상위 N개 선정."""
        logger = logger or self._logger
        t_start = self.pm.start_timer()
        logger.info({"event": "build_watchlist_started"})

        # 1) Pool A 로드
        if not self._pool_a_loaded:
            raw = self._load_premium_stocks()
            self._pool_a_items = {item.code: item for item in raw}
            self._pool_a_loaded = True

        # 2) 당일 급등주 빌드 (실시간 랭킹)
        pool_b_items = await self._build_daily_surge_pool(logger=logger)

        # 3) 병합
        merged: Dict[str, OSBWatchlistItem] = dict(self._pool_a_items)
        for code, item in pool_b_items.items():
            if code not in merged:
                merged[code] = item

        # 4) 정렬 및 절삭
        sorted_items = sorted(
            merged.values(),
            key=lambda x: (x.total_score, self._calc_turnover_ratio(x)),
            reverse=True,
        )

        # 스코어링 후 정렬된 상위 종목 로그
        top_n_for_log = 10
        logger.debug({
            "event": "watchlist_sorted",
            "top_n": top_n_for_log,
            "items": [
                {
                    "code": i.code, "name": i.name, "total_score": i.total_score,
                    "rs_score": i.rs_score, "profit_score": i.profit_growth_score,
                    "turnover": round(self._calc_turnover_ratio(i), 4)
                }
                for i in sorted_items[:top_n_for_log]
            ]
        })

        self._watchlist = {
            item.code: item for item in sorted_items[:self._cfg.max_watchlist]
        }
        logger.info({
            "event": "build_watchlist_finished",
            "premium_stocks": len(self._pool_a_items),
            "daily_surge_stocks": len(pool_b_items),
            "final_count": len(self._watchlist)
        })
        self.pm.log_timer("OneilUniverseService._build_watchlist", t_start, threshold=5.0)

    async def _build_daily_surge_pool(self, logger: Optional[logging.Logger] = None) -> Dict[str, OSBWatchlistItem]:
        """당일 급등주: 실시간 랭킹 기반 종목 발굴."""
        logger = logger or self._logger
        t_start = self.pm.start_timer()
        # 3가지 랭킹 병합
        trading_val_resp, rise_resp, volume_resp = await asyncio.gather(
            self._sqs.get_top_trading_value_stocks(),
            self._sqs.get_top_rise_fall_stocks(rise=True),
            self._sqs.get_top_volume_stocks(),
            return_exceptions=True,
        )

        candidate_map = {}
        for resp in [trading_val_resp, rise_resp, volume_resp]:
            if isinstance(resp, Exception) or not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                continue
            for stock in (resp.data or []):
                if isinstance(stock, dict):
                    code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
                    name = stock.get("hts_kor_isnm", "")
                else:
                    code = getattr(stock, "mksc_shrn_iscd", "") or getattr(stock, "stck_shrn_iscd", "")
                    name = getattr(stock, "hts_kor_isnm", "")
                if code:
                    candidate_map[code] = name

        # 분석 및 필터링
        items = []
        skip_codes = set(self._pool_a_items.keys()) | set(self._watchlist.keys())
        
        # [성능 개선] 순차 처리 -> 청크 단위 병렬 처리 (asyncio.gather)
        candidates = [(c, n) for c, n in candidate_map.items() if c not in skip_codes]
        logger.debug({
            "event": "daily_surge_candidates_collected",
            "raw_count": len(candidate_map),
            "skip_count": len(candidate_map) - len(candidates),
            "candidate_count": len(candidates),
        })
        
        for chunk in _chunked(candidates, self._cfg.api_chunk_size):
            tasks = [self._analyze_surge_candidate(code, name, logger=logger) for code, name in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in results:
                if isinstance(res, Exception) or res is None:
                    continue
                items.append(res)
            # 레이트 리밋 고려하여 약간의 대기 (필요 시)
            await asyncio.sleep(0.1)

        # 스코어링
        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        surge_rating_map = await self._fetch_rs_rating_map(today_str)
        self._compute_rs_scores(items, logger=logger, rating_map=surge_rating_map)
        # 2. 실적(스크래핑) 및 과거 3일 수급(API)은 장중 병목 방지 및
        # 당일 첫 급등주(Day-1) 포착을 위해 장 중에는 생략!
        # await self._compute_profit_growth_scores(items, logger=logger)
        # await self._compute_smart_money_scores(items, logger=logger)
        self._compute_total_scores(items, logger=logger)

        # 상위 N개
        items.sort(key=lambda x: (x.total_score, self._calc_turnover_ratio(x)), reverse=True)

        # Pool B 스코어링 후 정렬된 상위 종목 로그
        top_n_for_log = 10
        logger.debug({
            "event": "daily_surge_pool_sorted",
            "candidate_count": len(candidates),
            "selected_count": len(items),
            "top_n": top_n_for_log,
            "items": [
                {
                    "code": i.code, "name": i.name, "total_score": i.total_score,
                    "rs_score": i.rs_score, "profit_score": i.profit_growth_score,
                    "turnover": round(self._calc_turnover_ratio(i), 4)
                }
                for i in items[:top_n_for_log]
            ]
        })

        self.pm.log_timer("OneilUniverseService._build_daily_surge_pool", t_start, threshold=3.0)
        return {item.code: item for item in items[:self._cfg.daily_surge_size]}

    async def _analyze_premium_candidate(self, code: str, name: str, logger: Optional[logging.Logger] = None) -> Optional[OSBWatchlistItem]:
        """장마감 후 전일 기준 우량주(Pool A) 분석. 
        어제까지의 확정된 일봉 데이터만 사용하므로 별도의 슬라이싱이 필요하지 않습니다."""
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=260)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == ErrorCode.SUCCESS.value else []

        if not ohlcv:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "no_ohlcv"})
            return None

        period = self._cfg.high_breakout_period
        closes = [r.get("close", 0) for r in ohlcv if r.get("close") is not None]
        if len(closes) < 50:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "insufficient_data_len", "len": len(closes)})
            return None

        highs = [r.get("high", 0) for r in ohlcv[-period:] if r.get("high") is not None]
        volumes = [r.get("volume", 0) for r in ohlcv[-period:] if r.get("volume") is not None]

        if not highs or not volumes:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "missing_high_or_volume"})
            return None

        ma_20d = sum(closes[-20:]) / 20
        ma_50d = sum(closes[-50:]) / 50
        ma_150d = sum(closes[-150:]) / 150 if len(closes) >= 150 else 0.0
        ma_200d = sum(closes[-200:]) / 200 if len(closes) >= 200 else 0.0
        high_20d = int(max(highs))
        avg_vol_20d = sum(volumes) / len(volumes)
        prev_close = closes[-1]

        # 52주 저가: 장중 저가(low) 기준 — 미너비니 원칙
        lows_all = [r.get("low", 0) for r in ohlcv if r.get("low") is not None]
        w52_lows = lows_all[-252:] if len(lows_all) >= 252 else lows_all
        w52_lwpr = int(min(w52_lows)) if w52_lows else 0

        # 필터: 거래대금, 정배열
        recent_5 = ohlcv[-5:]
        tv_5d = sum([(r.get("volume", 0) * r.get("close", 0)) for r in recent_5]) / len(recent_5)
        if tv_5d < self._cfg.min_avg_trading_value_5d:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "low_trading_value", "value": tv_5d})
            return None
        if not (prev_close > ma_20d > ma_50d):
            if logger: logger.debug({"event": "drop", "code": code, "reason": "not_uptrend", "close": prev_close, "ma20": ma_20d, "ma50": ma_50d})
            return None
        
        if logger: logger.debug({"event": "pass_trend", "code": code, "reason": "uptrend_and_volume_ok"})

        # 필터: 52주 고가 근접
        full_resp = await self._sqs.get_current_price(code, caller="OneilUniverseService")
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "current_price_api_fail"})
            return None
        output = full_resp.data.get("output") if full_resp.data else None
        if not output:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "no_price_output"})
            return None
        
        if isinstance(output, dict):
            w52_hgpr = int(output.get("w52_hgpr") or 0)
            # hts_avls: 시가총액(억), stck_llam: 상장주식수(주) - 시가총액 우선 사용 및 억 단위 보정
            cap_billion = int(output.get("hts_avls") or output.get("stck_llam") or 0)
        else:
            w52_hgpr = int(getattr(output, "w52_hgpr", 0) or 0)
            cap_billion = int(getattr(output, "hts_avls", 0) or getattr(output, "stck_llam", 0) or 0)
        stck_llam = cap_billion * 100_000_000  # 억 단위 -> 원 단위 변환

        # Pool B 전용 시가총액 필터
        if not (self._cfg.premium_stocks_cap_min <= stck_llam <= self._cfg.premium_stocks_cap_max):
            if logger: logger.debug({"event": "drop", "code": code, "reason": "market_cap_out_of_range", "cap": stck_llam})
            return None

        dist = 0
        if w52_hgpr > 0:
            dist = ((w52_hgpr - prev_close) / w52_hgpr) * 100
            if dist > self._cfg.near_52w_high_pct:
                if logger: logger.debug({"event": "drop", "code": code, "reason": "far_from_52w_high", "dist": dist})
                return None
        
        if logger: logger.debug({"event": "pass_52w", "code": code, "dist": dist})

        # BB 스퀴즈 (동기 계산: async/await 오버헤드 제거)
        widths = self._indicator.calc_bb_widths_sync(
            ohlcv, period=self._cfg.bb_period, multiplier=self._cfg.multiplier
        )

        if len(widths) < period:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "insufficient_bb_data"})
            return None

        bb_min = min(widths[-period:])
        prev_width = widths[-1]

        # 스퀴즈 조건 체크 (전일 BB폭 <= 20일 최소폭 * 1.2)
        if prev_width > bb_min * self._cfg.squeeze_tolerance:
            if logger: logger.debug({
                "event": "drop", "code": code, "reason": "no_squeeze",
                "prev_width": prev_width, "bb_min": bb_min,
                "ratio": round(prev_width / bb_min, 2) if bb_min > 0 else 0
            })
            return None
        
        if logger: logger.debug({
            "event": "pass_squeeze", "code": code,
            "prev_width": prev_width, "bb_min": bb_min
        })
        
        # RS 계산 (동기 계산: async/await 오버헤드 제거)
        rs_return = self._indicator.calc_rs_sync(
            ohlcv, period_days=self._cfg.rs_period_days
        )

        market = "KOSDAQ" if self.stock_code_repository.is_kosdaq(code) else "KOSPI"

        # 미너비니 Stage 4 하드 필터 (MA200 데이터가 충분할 때만 적용)
        minervini_stage = 0
        if self._minervini_svc and len(closes) >= 200:
            minervini_stage = self._minervini_svc.classify_stage(closes, lows_all, rs_rating=0)
            if minervini_stage == 4:
                if logger: logger.debug({"event": "drop", "code": code, "reason": "minervini_stage4"})
                return None

        if logger: logger.debug({"event": "selected", "code": code, "name": name})

        return OSBWatchlistItem(
            code=code, name=name, market=market,
            high_20d=high_20d, ma_20d=ma_20d, ma_50d=ma_50d,
            avg_vol_20d=avg_vol_20d, bb_width_min_20d=bb_min, prev_bb_width=prev_width,
            w52_hgpr=w52_hgpr, avg_trading_value_5d=tv_5d, market_cap=stck_llam,
            rs_return_3m=rs_return,
            ma_150d=ma_150d, ma_200d=ma_200d, w52_lwpr=w52_lwpr, minervini_stage=minervini_stage,
            source="pool_a",
        )

    async def _analyze_surge_candidate(self, code: str, name: str, logger: Optional[logging.Logger] = None) -> Optional[OSBWatchlistItem]:
        """장중 실시간 급등주(Pool B) 분석.
        어제까지의 캐시된 OHLCV 데이터와 오늘의 실시간 가격을 결합하여 분석합니다.
        """

        # 1. 어제 날짜 계산 (전략의 _check_entry 패턴 적용)
        now = self._tm.get_current_kst_time()
        yesterday_str = (now - timedelta(days=1)).strftime("%Y%m%d")
        today_str = now.strftime("%Y%m%d")
        
        # 2. 어제까지의 OHLCV 조회 (end_date 지정 시 DB/캐시 히트율 상승)
        # limit은 분석에 필요한 ma_50d 등을 고려하여 90일로 유지
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(code, limit=90, end_date=yesterday_str)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == ErrorCode.SUCCESS.value else []

        if not ohlcv:
            return None

        # 3. 실시간 현재가/거래량 조회 (어차피 스캔 시 필요한 데이터)
        full_resp = await self._sqs.get_current_price(code, caller="OneilUniverseService")
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            return None
        output = full_resp.data.get("output")
        if not output:
            return None
        
        # 데이터 추출 (전략 패턴과 동일하게 안전하게 추출)
        if isinstance(output, dict):
            current = int(output.get("stck_prpr", 0))
            vol = int(output.get("acml_vol", 0))
            today_open = int(output.get("stck_oprc", 0))
            today_high = int(output.get("stck_hgpr", 0))
            today_low = int(output.get("stck_lwpr", 0))
        else:
            current = int(getattr(output, "stck_prpr", 0) or 0)
            vol = int(getattr(output, "acml_vol", 0) or 0)
            today_open = int(getattr(output, "stck_oprc", 0) or 0)
            today_high = int(getattr(output, "stck_hgpr", 0) or 0)
            today_low = int(getattr(output, "stck_lwpr", 0) or 0)

        # 4. 실시간 가상 캔들 합성 (Today Candle Injection)
        today_candle = {
            "date": today_str,
            "open": float(today_open),
            "high": float(today_high),
            "low": float(today_low),
            "close": float(current),
            "volume": vol,
        }
        
        # 혹시 API가 오늘 데이터를 포함했더라도 중복되지 않게 처리
        if ohlcv[-1].get("date") == today_str:
            ohlcv[-1] = today_candle
        else:
            ohlcv.append(today_candle)

        # 5. 이제 실시간 데이터가 포함된 ohlcv를 사용하여 정량 분석 진행
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        if len(closes) < 50:
            return None

        period = self._cfg.high_breakout_period
        highs = [r.get("high", 0) for r in ohlcv[-period:] if r.get("high") is not None]
        volumes = [r.get("volume", 0) for r in ohlcv[-period:] if r.get("volume") is not None]

        if not highs or not volumes:
            return None

        # 지표 계산 (오늘의 실시간 변동이 반영됨)
        ma_20d = sum(closes[-20:]) / 20
        ma_50d = sum(closes[-50:]) / 50
        # prev_close는 '어제 종가'여야 하므로 리스트의 마지막에서 두 번째(-2) 사용
        prev_close = closes[-2]

        high_20d = int(max(highs))
        avg_vol_20d = sum(volumes) / len(volumes)

        # 필터: 거래대금 (최근 5일 = 어제까지 4일 + 오늘 실시간 1일)
        recent_5 = ohlcv[-5:]
        tv_5d = sum([(r.get("volume", 0) * r.get("close", 0)) for r in recent_5]) / len(recent_5)
        
        if tv_5d < self._cfg.daily_surge_min_avg_trading_value_5d:
            if logger: logger.debug({
                "event": "drop", "code": code, "reason": "daily_surge_low_trading_value",
                "value": tv_5d, "threshold": self._cfg.daily_surge_min_avg_trading_value_5d
            })
            return None

        # 필터: 정배열 (실시간 주가 반영)
        if not (current > ma_20d > ma_50d):
            if logger: logger.debug({
                "event": "drop", "code": code, "reason": "not_uptrend",
                "current": current, "ma20": ma_20d, "ma50": ma_50d
            })
            return None

        # 필터: 52주 고가 근접
        full_resp = await self._sqs.get_current_price(code, caller="OneilUniverseService")
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "current_price_api_fail"})
            return None
        output = full_resp.data.get("output") if full_resp.data else None
        if not output:
            if logger: logger.debug({"event": "drop", "code": code, "reason": "no_price_output"})
            return None
        
        if isinstance(output, dict):
            w52_hgpr = int(output.get("w52_hgpr") or 0)
            cap_billion = int(output.get("hts_avls") or output.get("stck_llam") or 0)
        else:
            w52_hgpr = int(getattr(output, "w52_hgpr", 0) or 0)
            cap_billion = int(getattr(output, "hts_avls", 0) or getattr(output, "stck_llam", 0) or 0)
        stck_llam = cap_billion * 100_000_000

        # 필터: 시가총액 (1000억 ~ 100조)
        if not (self._cfg.daily_surge_cap_min <= stck_llam <= self._cfg.daily_surge_cap_max):
            if logger: logger.debug({
                "event": "drop", "code": code, "reason": "daily_surge_market_cap_out_of_range",
                "cap": stck_llam, "min": self._cfg.daily_surge_cap_min, "max": self._cfg.daily_surge_cap_max
            })
            return None

        dist = 0
        if w52_hgpr > 0:
            dist = ((w52_hgpr - prev_close) / w52_hgpr) * 100
            if dist > self._cfg.near_52w_high_pct:
                if logger: logger.debug({"event": "drop", "code": code, "reason": "far_from_52w_high", "dist": dist})
                return None

        # BB 폭 계산 (Pool B는 squeeze 조건 미적용 — 급등 중 종목은 변동성 확장 단계라 squeeze 자체가 본질에 부적합)
        widths = self._indicator.calc_bb_widths_sync(ohlcv[:-1], period=self._cfg.bb_period, multiplier=self._cfg.multiplier)
        if len(widths) < period:
            return None
        bb_min = min(widths[-period:])
        prev_width = widths[-1]

        # RS 계산
        rs_return = self._indicator.calc_rs_sync(ohlcv[:-1], period_days=self._cfg.rs_period_days)
        market = "KOSDAQ" if self.stock_code_repository.is_kosdaq(code) else "KOSPI"

        if logger: logger.debug({"event": "selected_surge", "code": code, "name": name})

        return OSBWatchlistItem(
            code=code, name=name, market=market,
            high_20d=high_20d, ma_20d=ma_20d, ma_50d=ma_50d,
            avg_vol_20d=avg_vol_20d, bb_width_min_20d=bb_min, prev_bb_width=prev_width,
            w52_hgpr=w52_hgpr, avg_trading_value_5d=tv_5d, market_cap=stck_llam,
            rs_return_3m=rs_return,
            source="pool_b",
        )

    # ── 전일 기준 우량주 생성 (배치) ─────────────────────────────────────────

    async def generate_premium_watchlist(self, trading_date: Optional[str] = None) -> dict:
        """전체 종목 스캔 -> 전일 기준 우량주 생성 및 파일 저장.

        Args:
            trading_date: 기준 거래일(YYYYMMDD). 지정하면 파일의 generated_date로 저장.
                          None이면 현재 날짜를 사용 (직접 호출 시 하위 호환).
        """
        # 전용 로거 생성 (logs/strategies/oneil/YYYYMMDD_HHMMSS_generate_premium_watchlist.log.json)
        pool_a_logger = get_strategy_logger("generate_premium_watchlist", sub_dir="oneil_pool")
        pool_a_logger.setLevel(logging.DEBUG)

        self._logger.info({"event": "generate_premium_watchlist_started"})
        pool_a_logger.info({"event": "generate_premium_watchlist_started"})

        start_time = time.time()
        start_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time))
        
        # 1. 전체 종목 로드
        all_stocks = []
        for _, row in self.stock_code_repository.df.iterrows():
            code = row.get("종목코드", "")
            name = row.get("종목명", "")
            market = row.get("시장구분", "")
            if code and market in ("KOSPI", "KOSDAQ"):
                all_stocks.append((code, name, market))

        total_stocks = len(all_stocks)
        print(f"[전일 기준 우량주 생성] 시작시간: {start_time_str} | 전체 종목 수: {total_stocks}개. 1차 필터링(시총) 시작...")
        pool_a_logger.info({"event": "1st_filter_start", "total_stocks": total_stocks})
        self._generation_progress = {
            "running": True, "phase": "1차_필터(시총)",
            "processed": 0, "total": total_stocks,
            "passed": 0, "selected": 0, "elapsed": 0.0,
        }

        # 2. 1차 필터 (시총)
        passed_first = []
        processed_count = 0
        for chunk in _chunked(all_stocks, self._cfg.api_chunk_size):
            tasks = [self._sqs.get_current_price(c, caller="OneilUniverseService") for c, _, _ in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (code, name, market), resp in zip(chunk, results):
                if isinstance(resp, Exception) or not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                    error_msg = str(resp) if isinstance(resp, Exception) else (getattr(resp, 'msg1', 'No response') if resp else 'Empty response')
                    pool_a_logger.warning({
                        "event": "api_error_1st_filter", 
                        "code": code, 
                        "name": name, 
                        "error": error_msg
                    })
                    continue
                out = resp.data.get("output") if resp.data else None
                if not out: continue
                
                if isinstance(out, dict):
                    val_avls = out.get("hts_avls")
                    val_llam = out.get("stck_llam")
                else:
                    val_avls = getattr(out, "hts_avls", None)
                    val_llam = getattr(out, "stck_llam", None)
                
                if val_avls:
                    cap = int(val_avls) * 100_000_000
                else:
                    # Fallback: stck_llam 사용 (테스트 호환성 위해 큰 값은 원 단위로 처리)
                    val = int(val_llam or 0)
                    cap = val if val > 100_000_000 else val * 100_000_000
                
                if self._cfg.premium_stocks_cap_min <= cap <= self._cfg.premium_stocks_cap_max:
                    passed_first.append((code, name, market))
                    pool_a_logger.debug({"event": "pass_1st", "code": code, "name": name, "market_cap(억)": cap/100_000_000})
                else:
                    # pool_a_logger.debug({"event": "drop_1st", "code": code, "reason": "market_cap", "cap": cap})
                    pass
            
            processed_count += len(chunk)
            if processed_count % 100 == 0 or processed_count >= total_stocks:
                pct = (processed_count / total_stocks * 100) if total_stocks > 0 else 0.0
                elapsed = time.time() - start_time
                print(f"  > [1차 필터] 진행: {processed_count}/{total_stocks} ({pct:.1f}%) | 통과: {len(passed_first)} | 소요: {elapsed:.1f}s")
                pool_a_logger.info({"event": "1st_filter_progress", "processed": processed_count, "total": total_stocks, "passed": len(passed_first)})
                self._generation_progress.update({
                    "processed": processed_count, "passed": len(passed_first), "elapsed": round(elapsed, 1),
                })


        print(f"[전일 기준 우량주 생성] 1차 필터 완료. 통과: {len(passed_first)}개. 2차 상세 분석(OHLCV/지표) 시작...")
        pool_a_logger.info({"event": "1st_filter_done", "passed": len(passed_first)})
        pool_a_logger.info({"event": "2nd_filter_start", "total_candidates": len(passed_first)})
        self._generation_progress.update({
            "phase": "2차_필터(지표)", "processed": 0, "total": len(passed_first), "selected": 0,
        })

        # 3. 2차 필터 (상세 분석)
        items = []
        total_passed = len(passed_first)
        processed_count_2 = 0
        for chunk in _chunked(passed_first, self._cfg.api_chunk_size):
            for code, name, market in chunk:
                item = await self._analyze_premium_candidate(code, name, logger=pool_a_logger)
                if item:
                    items.append(item)
            
            processed_count_2 += len(chunk)
            if processed_count_2 % 50 == 0 or processed_count_2 >= total_passed:
                pct2 = (processed_count_2 / total_passed * 100) if total_passed > 0 else 0.0
                elapsed = time.time() - start_time
                print(f"  > [2차 필터] 진행: {processed_count_2}/{total_passed} ({pct2:.1f}%) | 선정: {len(items)} | 소요: {elapsed:.1f}s")
                pool_a_logger.info({"event": "2nd_filter_progress", "processed": processed_count_2, "total": total_passed, "selected": len(items)})
                self._generation_progress.update({
                    "processed": processed_count_2, "selected": len(items), "elapsed": round(elapsed, 1),
                })


        pool_a_logger.info({"event": "2nd_filter_done", "selected": len(items)})
        self._generation_progress.update({"phase": "스코어링"})

        # 4. 스코어링 및 저장
        pool_a_rating_map = await self._fetch_rs_rating_map(trading_date)
        self._compute_rs_scores(items, logger=pool_a_logger, rating_map=pool_a_rating_map)
        await self._compute_profit_growth_scores(items, logger=pool_a_logger)
        await self._compute_smart_money_scores(items, logger=pool_a_logger, date=trading_date)
        self._compute_total_scores(items, logger=pool_a_logger)
        pool_a_logger.info({"event": "scoring_done"})

        sort_key = lambda x: (x.total_score, self._calc_turnover_ratio(x))
        kospi = sorted([i for i in items if i.market != "KOSDAQ"], key=sort_key, reverse=True)[:self._cfg.premium_stocks_kospi_size]
        kosdaq = sorted([i for i in items if i.market == "KOSDAQ"], key=sort_key, reverse=True)[:self._cfg.premium_stocks_kosdaq_size]

        self._save_premium_stocks(kospi, kosdaq, trading_date=trading_date)
        pool_a_logger.info({"event": "save_done", "kospi_count": len(kospi), "kosdaq_count": len(kosdaq)})

        total_elapsed = time.time() - start_time
        print(f"[전일 기준 우량주 생성] 완료. 총 소요시간: {total_elapsed:.1f}초")
        pool_a_logger.info({"event": "generate_premium_watchlist_finished", "elapsed_seconds": total_elapsed})
        self._generation_progress.update({"running": False, "phase": None, "elapsed": round(total_elapsed, 1)})

        # 시총 범위 문자열 생성 (예: 2000억 ~ 2조)
        min_cap = self._cfg.premium_stocks_cap_min // 100000000
        max_cap = self._cfg.premium_stocks_cap_max // 100000000
        cap_str = f"{min_cap}억 ~ {max_cap}억"
        if self._cfg.premium_stocks_cap_max >= 1000000000000:
             cap_str = f"{min_cap}억 ~ {self._cfg.premium_stocks_cap_max // 1000000000000}조"

        return {
            "kospi_count": len(kospi), "kosdaq_count": len(kosdaq),
            "kospi_stocks": [asdict(i) for i in kospi],
            "kosdaq_stocks": [asdict(i) for i in kosdaq],
            "total_scanned": len(all_stocks), "scanned": len(all_stocks),
            "passed_first": len(passed_first), "first_filter_passed": len(passed_first),
            "second_filter_passed": len(items),
            "market_cap_filter": cap_str,
            "total_elapsed_seconds": total_elapsed
        }

    # ── 헬퍼 메서드 ───────────────────────────────────────────────

    def _should_refresh_watchlist(self) -> bool:
        now = self._tm.get_current_kst_time()
        open_time = self._tm.get_market_open_time()
        elapsed = (now - open_time).total_seconds() / 60
        
        triggered = False
        for t_min in self._cfg.watchlist_refresh_minutes:
            if elapsed >= t_min and t_min not in self._watchlist_refresh_done:
                self._watchlist_refresh_done.add(t_min)
                triggered = True
        return triggered

    async def _update_market_timing(self, caller: str = "", logger: Optional[logging.Logger] = None):
        logger = logger or self._logger
        for market, code in [("KOSDAQ", self._cfg.kosdaq_etf_code), ("KOSPI", self._cfg.kospi_etf_code)]:
            is_rising, fail_detail, ma_values = await self._check_etf_ma_rising(code, logger=logger)
            self._market_timing_cache[market] = is_rising

            logger.info({
                "event": "market_timing_updated",
                "market": market,
                "ok": is_rising,
                "fail_reason": fail_detail if not is_rising else "",
            })

            if self._notification_service:
                status_text = "🟢 매수 적합 (우상향)" if is_rising else "🔴 매수 부적합 (추세 꺾임)"
                level = NotificationLevel.INFO if is_rising else NotificationLevel.WARNING
                
                ma_str = " ➔ ".join([f"{v:.2f}" for v in ma_values])
                msg = f"• 지수: {market} ({code})\n• 상태: {status_text}\n"
                if not is_rising and fail_detail:
                    msg += f"• 사유: {fail_detail}\n"
                msg += f"• 최근 MA(20) 추이: {ma_str}"
                
                title = f"마켓 타이밍 갱신 ({market})"
                if caller:
                    title = f"[{caller}] {title}"
                
                await self._notification_service.emit(
                    category=NotificationCategory.STRATEGY,
                    level=level,
                    title=title,
                    message=msg,
                    metadata={
                        "force_external": True,
                        "event": "market_timing_updated",
                        "market": market,
                    },
                )

    async def _check_etf_ma_rising(self, etf_code: str, logger: Optional[logging.Logger] = None) -> Tuple[bool, str, List[float]]:
        logger = logger or self._logger
        period = self._cfg.market_ma_period
        days = self._cfg.market_ma_rising_days
        ohlcv_resp = await self._sqs.get_recent_daily_ohlcv(etf_code, limit=period + days + 5)
        ohlcv = ohlcv_resp.data if ohlcv_resp and ohlcv_resp.rt_cd == ErrorCode.SUCCESS.value else []

        if not ohlcv or len(ohlcv) < period + days:
            return False, "insufficient data", []

        closes = [r.get("close", 0) for r in ohlcv]
        if len(closes) < period + days:
            return False, "insufficient close data", []

        ma_values = []
        for i in range(days + 1):
            end = len(closes) - days + i
            ma_values.append(sum(closes[end-period:end]) / period)

        daily_changes_pct = []
        for j in range(1, len(ma_values)):
            prev = ma_values[j - 1]
            curr = ma_values[j]
            daily_changes_pct.append(((curr - prev) / prev * 100) if prev else 0.0)

        first_ma = ma_values[0]
        last_ma = ma_values[-1]
        net_change_pct = ((last_ma - first_ma) / first_ma * 100) if first_ma else 0.0
        max_daily_drop_pct = min(daily_changes_pct) if daily_changes_pct else 0.0
        worst_drop_idx = daily_changes_pct.index(max_daily_drop_pct) + 1 if daily_changes_pct else 0

        min_net_change_pct = getattr(self._cfg, "market_ma_min_net_change_pct", -0.10)
        daily_dip_tolerance_pct = getattr(self._cfg, "market_ma_daily_dip_tolerance_pct", -0.20)
        hard_decline_pct = getattr(self._cfg, "market_ma_hard_decline_pct", -0.50)

        is_rising = True
        fail_detail = ""
        trend_status = "rising"
        if max_daily_drop_pct < hard_decline_pct:
            is_rising = False
            fail_detail = (
                f"MA hard decline: {max_daily_drop_pct:.2f}% < {hard_decline_pct:.2f}% "
                f"(idx {worst_drop_idx}, {ma_values[worst_drop_idx-1]:.2f} -> {ma_values[worst_drop_idx]:.2f})"
            )
            trend_status = "hard_decline"
        elif net_change_pct < min_net_change_pct:
            is_rising = False
            fail_detail = (
                f"MA trend weak: net {net_change_pct:.2f}% < {min_net_change_pct:.2f}% "
                f"({first_ma:.2f} -> {last_ma:.2f})"
            )
            trend_status = "weak_trend"
        elif max_daily_drop_pct < daily_dip_tolerance_pct:
            trend_status = "uptrend_under_pressure"

        log_data = {
            "event": "market_timing_check",
            "etf_code": etf_code,
            "is_rising": is_rising,
            "trend_status": trend_status,
            "ma_period": period,
            "lookback_days": days,
            "ma_values": [round(v, 2) for v in ma_values],
            "daily_changes_pct": [round(v, 3) for v in daily_changes_pct],
            "net_change_pct": round(net_change_pct, 3),
            "max_daily_drop_pct": round(max_daily_drop_pct, 3),
            "thresholds": {
                "min_net_change_pct": min_net_change_pct,
                "daily_dip_tolerance_pct": daily_dip_tolerance_pct,
                "hard_decline_pct": hard_decline_pct,
            },
        }
        if not is_rising:
            log_data["fail_detail"] = fail_detail

        logger.debug(log_data)

        return is_rising, fail_detail, ma_values

    def _compute_rs_scores(
        self,
        items: List[OSBWatchlistItem],
        logger: Optional[logging.Logger] = None,
        rating_map: Optional[Dict[str, int]] = None,
    ):
        """RS 스코어 계산.

        rating_map이 제공되면 DB에서 가져온 1~99 IBD/오닐 RS Rating을 이용한 연속 점수,
        없으면 기존 백분위 이진 점수(0 또는 rs_score_points) 방식으로 폴백.
        """
        logger = logger or self._logger
        if not items:
            return
        logger.debug({"event": "compute_rs_scores_started", "item_count": len(items), "mode": "rating" if rating_map else "percentile"})

        if rating_map:
            # ── RS Rating 모드 (1~99 연속 점수) ─────────────────────────
            rs_rating_min = getattr(self._cfg, "rs_rating_min", 0)
            for item in items:
                rating = rating_map.get(item.code, 0)
                item.rs_rating = rating
                # 연속 점수: rating / 99 × rs_score_points (최대 = rs_score_points)
                item.rs_score = round(rating / 99 * self._cfg.rs_score_points, 2) if rating > 0 else 0.0
                if rating > 0:
                    logger.debug({
                        "event": "rs_score_assigned", "code": item.code, "name": item.name,
                        "rs_rating": rating, "score": item.rs_score, "mode": "rating",
                    })
            logger.debug({
                "event": "compute_rs_scores_finished", "mode": "rating",
                "items_with_rating": sum(1 for i in items if i.rs_rating > 0),
                "rs_rating_min_cfg": rs_rating_min,
            })
        else:
            # ── 폴백: 기존 백분위 이진 점수 ─────────────────────────────
            rets = sorted([i.rs_return_3m for i in items])
            percentile_index = min(int(len(rets) * (1 - self._cfg.rs_top_percentile / 100)), len(rets) - 1)
            cutoff = rets[percentile_index]

            logger.debug({
                "event": "rs_score_calculation_details",
                "item_count": len(items),
                "top_percentile_config": self._cfg.rs_top_percentile,
                "cutoff_return": round(cutoff, 2),
                "returns_distribution": {
                    "min": round(rets[0], 2),
                    "p25": round(rets[int(len(rets) * 0.25)], 2),
                    "median": round(rets[int(len(rets) * 0.5)], 2),
                    "p75": round(rets[int(len(rets) * 0.75)], 2),
                    "max": round(rets[-1], 2),
                },
                "mode": "percentile_fallback",
            })

            for item in items:
                is_top_tier = item.rs_return_3m >= cutoff
                item.rs_score = self._cfg.rs_score_points if is_top_tier else 0.0
                if is_top_tier:
                    logger.debug({
                        "event": "rs_score_assigned", "code": item.code, "name": item.name,
                        "return_3m": round(item.rs_return_3m, 2), "score": item.rs_score,
                    })
            logger.debug({"event": "compute_rs_scores_finished", "mode": "percentile_fallback"})

    async def _fetch_rs_rating_map(self, trade_date: str) -> Optional[Dict[str, int]]:
        """RS Rating 서비스에서 해당 날짜의 {code: rs_rating} 맵을 조회합니다.
        서비스가 없거나 데이터가 없으면 None 반환 → 폴백(이진 점수) 사용.
        """
        if not self._rs_rating_service:
            return None
        try:
            resp = await self._rs_rating_service.get_ratings_by_date(trade_date)
            if resp.rt_cd == "0" and resp.data:
                return resp.data
        except Exception as e:
            self._logger.warning(f"OneilUniverseService: RS Rating 조회 실패 ({e}) — 이진 스코어로 폴백")
        return None

    async def _compute_profit_growth_scores(self, items: List[OSBWatchlistItem], logger: Optional[logging.Logger] = None):
        logger = logger or self._logger
        if not items: return
        logger.debug({"event": "compute_profit_growth_scores_started", "item_count": len(items)})
        
        for chunk in _chunked(items, self._cfg.api_chunk_size):
            # API 대신 스크래퍼의 메서드를 호출
            tasks = [self._scraper.fetch_yoy_profit_growth(i.code) for i in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for item, growth in zip(chunk, results):
                if isinstance(growth, Exception):
                    logger.warning({"event": "profit_growth_scraping_error", "code": item.code, "error": str(growth)})
                    item.profit_growth_score = 0.0
                    continue

                # 턴어라운드(999.0)이거나 설정한 한계치 이상의 성장이면 스코어 부여
                if growth >= self._cfg.profit_growth_threshold_pct or growth == 999.0:
                    item.profit_growth_score = self._cfg.profit_growth_score_points
                    logger.debug({
                        "event": "profit_growth_score_assigned", 
                        "code": item.code, 
                        "name": item.name,
                        "growth_pct": "Turnaround" if growth == 999.0 else round(growth, 2), 
                        "score": item.profit_growth_score
                    })
                else:
                    item.profit_growth_score = 0.0

        logger.debug({"event": "compute_profit_growth_scores_finished"})

    async def _compute_smart_money_scores(self, items: List[OSBWatchlistItem], logger: Optional[logging.Logger] = None, date: Optional[str] = None):
        """3일 누적 외국인+기관 순매수금액 기반 스마트머니 스코어링.

        조건 A: 3일 누적 (외국인 + 기관 순매수금액) >= 시총의 smart_money_to_mcap_pct%
        조건 B: 3일 누적 (외국인 + 기관 순매수금액) >= 3일 누적 총거래대금의 smart_money_to_tv_pct%
        A 또는 B 만족 시 smart_money_score_points 부여.

        단위: frgn/orgn_ntby_tr_pbmn 은 백만원 → *1_000_000 = 원, acml_tr_pbmn 은 원.
        """
        logger = logger or self._logger
        if not items: return
        days = self._cfg.smart_money_lookback_days
        logger.debug({"event": "compute_smart_money_scores_started", "item_count": len(items), "lookback_days": days})

        for chunk in _chunked(items, self._cfg.api_chunk_size):
            tasks = [self._sqs.get_investor_trade_daily_multi(i.code, date, days) for i in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for item, resp in zip(chunk, results):
                item.smart_money_score = 0.0
                if isinstance(resp, Exception):
                    logger.warning({"event": "smart_money_api_error", "code": item.code, "error": str(resp)})
                    continue
                if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                    continue

                rows = resp.data  # list of dicts, newest first
                sum_fi_won = 0.0   # 3일 누적 외국인+기관 순매수금액 (원)
                sum_tv_won = 0.0   # 3일 누적 총거래대금 (원)
                for row in rows:
                    frgn = float(row.get("frgn_ntby_tr_pbmn", "0") or "0")
                    orgn = float(row.get("orgn_ntby_tr_pbmn", "0") or "0")
                    tv   = float(row.get("acml_tr_pbmn", "0") or "0")
                    sum_fi_won += (frgn + orgn) * 1_000_000  # 백만원 → 원
                    sum_tv_won += tv                           # 이미 원 단위

                mcap = float(item.market_cap) if item.market_cap else 0.0
                cond_a = mcap > 0 and sum_fi_won >= mcap * (self._cfg.smart_money_to_mcap_pct / 100.0)
                cond_b = sum_tv_won > 0 and sum_fi_won >= sum_tv_won * (self._cfg.smart_money_to_tv_pct / 100.0)

                if cond_a or cond_b:
                    item.smart_money_score = self._cfg.smart_money_score_points
                    logger.debug({
                        "event": "smart_money_score_assigned",
                        "code": item.code, "name": item.name,
                        "sum_fi_억": round(sum_fi_won / 1e8, 2),
                        "mcap_억": round(mcap / 1e8, 2),
                        "sum_tv_억": round(sum_tv_won / 1e8, 2),
                        "cond_a": cond_a, "cond_b": cond_b,
                        "score": item.smart_money_score,
                    })

        logger.debug({"event": "compute_smart_money_scores_finished"})

    def _compute_total_scores(self, items: List[OSBWatchlistItem], logger: Optional[logging.Logger] = None):
        logger = logger or self._logger
        if not items: return
        logger.debug({"event": "compute_total_scores_started", "item_count": len(items)})
        for item in items:
            item.total_score = item.rs_score + item.profit_growth_score + item.smart_money_score
            # 미너비니 Stage 2 가산점: 트렌드 템플릿 충족 종목 우선
            if item.minervini_stage == 2:
                item.total_score += 20.0
            if item.total_score > 0:
                logger.debug({
                    "event": "total_score_calculated", "code": item.code, "name": item.name,
                    "rs_score": item.rs_score, "profit_score": item.profit_growth_score,
                    "smart_money_score": item.smart_money_score,
                    "minervini_stage": item.minervini_stage,
                    "total_score": item.total_score
                })
        logger.debug({"event": "compute_total_scores_finished"})

    def _save_premium_stocks(self, kospi, kosdaq, trading_date: Optional[str] = None):
        """전일 기준 우량주를 파일에 저장한다.

        Args:
            trading_date: 기준 거래일(YYYYMMDD). generated_date 필드에 기록.
                          None이면 현재 날짜를 사용.
        """
        try:
            os.makedirs(os.path.dirname(self._cfg.premium_stocks_file), exist_ok=True)
            now = self._tm.get_current_kst_time()
            data = {
                # generated_date: 어떤 거래일 기준으로 생성됐는지 (스킵 로직의 기준)
                "generated_date": trading_date or now.strftime("%Y%m%d"),
                # generated_at: 실제 파일을 저장한 시각 (주말/공휴일에 생성 가능)
                "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "kospi": [asdict(i) for i in kospi],
                "kosdaq": [asdict(i) for i in kosdaq]
            }
            with open(self._cfg.premium_stocks_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._logger.error(f"Failed to save premium stocks: {e}")

    def _load_premium_stocks(self) -> List[OSBWatchlistItem]:
        if not os.path.exists(self._cfg.premium_stocks_file):
            return []
        try:
            with open(self._cfg.premium_stocks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 날짜 체크 (오늘/어제만 유효)
            gen_date = data.get("generated_date", "")

            try:
                gen_dt = datetime.strptime(gen_date, "%Y%m%d").date()
                curr_dt = self._tm.get_current_kst_time().date()
                # 7일 이내만 유효 (한국 최장 연휴 5일 + 여유)
                # generated_date는 거래일 기준이므로 월요일에 금요일 파일도 유효
                if (curr_dt - gen_dt).days > 7:
                    return []
            except ValueError:
                return []

            items = []
            for k in ["kospi", "kosdaq"]:
                for d in data.get(k, []):
                    items.append(OSBWatchlistItem(**d))
            return items
        except Exception:
            return []

    def get_premium_stocks_meta(self) -> Optional[dict]:
        """저장된 전일 기준 우량주 파일의 메타데이터 반환. 파일 없으면 None."""
        if not os.path.exists(self._cfg.premium_stocks_file):
            return None
        try:
            with open(self._cfg.premium_stocks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "generated_date": data.get("generated_date"),
                "generated_at": data.get("generated_at"),
            }
        except Exception:
            return None

    @staticmethod
    def _calc_turnover_ratio(item: OSBWatchlistItem) -> float:
        """회전율 계산: (5일 평균 거래대금 / 시가총액). 동점자 처리용."""
        return (item.avg_trading_value_5d / item.market_cap) if item.market_cap > 0 else 0

    @staticmethod
    def _extract_op_profit_growth(data) -> float:
        """API 응답에서 영업이익 증가율 추출.

        resp.data 구조: {"rt_cd": "0", "output": [{"stac_yymm": "...", "bsop_prfi_inrt": "...", ...}]}
        output 리스트의 첫 번째 항목(최신 분기)에서 영업이익 관련 필드를 탐색.
        """
        try:
            # API 응답 dict에서 output 리스트 추출
            if isinstance(data, dict):
                output = data.get("output", data)
            else:
                output = data

            target = output[0] if isinstance(output, list) and output else output
            if isinstance(target, dict):
                for k in ["bsop_prti_icdc", "sale_totl_prfi_icdc", "op_profit_growth", "bsop_prfi_inrt", "grs"]:
                    if val := target.get(k): return float(val)
        except: pass
        return 0.0
