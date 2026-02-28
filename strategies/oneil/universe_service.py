# strategies/oneil/universe_service.py
import asyncio
import json
import logging
import os
from dataclasses import asdict
from typing import Dict, List, Optional

from common.types import ErrorCode
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from market_data.stock_code_mapper import StockCodeMapper
from core.time_manager import TimeManager
from strategies.oneil.common_types import OneilUniverseConfig, OSBWatchlistItem


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


class OneilUniverseService:
    """오닐 전략 유니버스 관리 서비스.
    
    역할:
      1. Pool A (전일 기준 우량주) 생성 및 로드
      2. Pool B (당일 급등주) 실시간 발굴
      3. Watchlist (감시 대상 60종목) 병합 및 제공
      4. 마켓 타이밍 판단
    """

    def __init__(
        self,
        trading_service: TradingService,
        stock_query_service: StockQueryService,
        indicator_service: IndicatorService,
        stock_code_mapper: StockCodeMapper,
        time_manager: TimeManager,
        config: Optional[OneilUniverseConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._sqs = stock_query_service
        self._indicator = indicator_service
        self._mapper = stock_code_mapper
        self._tm = time_manager
        self._cfg = config or OneilUniverseConfig()
        self._logger = logger or logging.getLogger(__name__)

        # 상태 관리
        self._watchlist: Dict[str, OSBWatchlistItem] = {}
        self._watchlist_date: str = ""
        self._watchlist_refresh_done: set = set()
        self._pool_a_loaded: bool = False
        self._pool_a_items: Dict[str, OSBWatchlistItem] = {}
        
        # 마켓 타이밍 캐시
        self._market_timing_cache: Dict[str, bool] = {}
        self._market_timing_date: str = ""

    async def get_watchlist(self) -> Dict[str, OSBWatchlistItem]:
        """현재 유효한 워치리스트를 반환 (캐싱 + 자동 갱신)."""
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        
        # 날짜 변경 시 초기화
        if self._watchlist_date != today:
            self._watchlist_refresh_done = set()
            self._pool_a_loaded = False
            self._pool_a_items = {}
            await self._build_watchlist()
            self._watchlist_date = today
            self._market_timing_date = "" # 마켓타이밍도 재확인 필요
        
        # 장중 갱신 주기 체크
        elif self._should_refresh_watchlist():
            await self._build_watchlist()

        return self._watchlist

    async def is_market_timing_ok(self, market: str) -> bool:
        """해당 시장(KOSPI/KOSDAQ)의 마켓 타이밍이 매수 적합한지 확인."""
        today = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if self._market_timing_date != today:
            await self._update_market_timing()
            self._market_timing_date = today
        
        return self._market_timing_cache.get(market, False)

    # ── 워치리스트 빌드 ────────────────────────────────────────────

    async def _build_watchlist(self):
        """Pool A + Pool B 병합 -> 스코어링 -> 상위 N개 선정."""
        self._logger.info({"event": "build_watchlist_started"})

        # 1) Pool A 로드
        if not self._pool_a_loaded:
            raw = self._load_pool_a()
            self._pool_a_items = {item.code: item for item in raw}
            self._pool_a_loaded = True

        # 2) Pool B 빌드 (실시간 랭킹)
        pool_b_items = await self._build_pool_b()

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
        self._watchlist = {
            item.code: item for item in sorted_items[:self._cfg.max_watchlist]
        }
        
        self._logger.info({
            "event": "build_watchlist_finished",
            "pool_a": len(self._pool_a_items),
            "pool_b": len(pool_b_items),
            "final_count": len(self._watchlist)
        })

    async def _build_pool_b(self) -> Dict[str, OSBWatchlistItem]:
        """Pool B: 실시간 랭킹 기반 종목 발굴."""
        # 3가지 랭킹 병합
        trading_val_resp, rise_resp, volume_resp = await asyncio.gather(
            self._ts.get_top_trading_value_stocks(),
            self._ts.get_top_rise_fall_stocks(rise=True),
            self._ts.get_top_volume_stocks(),
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
        
        for code, name in candidate_map.items():
            if code in skip_codes:
                continue
            try:
                item = await self._analyze_candidate(code, name)
                if item:
                    items.append(item)
            except Exception:
                continue

        # 스코어링
        self._compute_rs_scores(items)
        await self._compute_profit_growth_scores(items)
        self._compute_total_scores(items)

        # 상위 N개
        items.sort(key=lambda x: (x.total_score, self._calc_turnover_ratio(x)), reverse=True)
        return {item.code: item for item in items[:self._cfg.pool_b_size]}

    async def _analyze_candidate(self, code: str, name: str) -> Optional[OSBWatchlistItem]:
        """개별 종목 분석 (OHLCV, BB, RS 등)."""
        ohlcv = await self._ts.get_recent_daily_ohlcv(code, limit=90)
        if not ohlcv or len(ohlcv) < 50:
            return None

        period = self._cfg.high_breakout_period
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        highs = [r.get("high", 0) for r in ohlcv[-period:] if r.get("high")]
        volumes = [r.get("volume", 0) for r in ohlcv[-period:] if r.get("volume")]

        ma_20d = sum(closes[-20:]) / 20
        ma_50d = sum(closes[-50:]) / 50
        high_20d = int(max(highs))
        avg_vol_20d = sum(volumes) / len(volumes)
        prev_close = closes[-1]

        # 필터: 거래대금, 정배열
        recent_5 = ohlcv[-5:]
        tv_5d = sum([(r.get("volume",0)*r.get("close",0)) for r in recent_5]) / len(recent_5)
        if tv_5d < self._cfg.min_avg_trading_value_5d:
            return None
        if not (prev_close > ma_20d > ma_50d):
            return None

        # 필터: 52주 고가 근접
        full_resp = await self._ts.get_current_stock_price(code)
        if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
            return None
        output = full_resp.data.get("output") if full_resp.data else None
        if not output:
            return None
        
        w52_hgpr = int(output.get("w52_hgpr") or 0)
        stck_llam = int(output.get("stck_llam") or 0)
        if w52_hgpr > 0:
            dist = ((w52_hgpr - prev_close) / w52_hgpr) * 100
            if dist > self._cfg.near_52w_high_pct:
                return None

        # BB 스퀴즈
        bb_resp = await self._indicator.get_bollinger_bands(
            code, period=self._cfg.bb_period, std_dev=self._cfg.bb_std_dev, ohlcv_data=ohlcv
        )
        widths = []
        for band in (bb_resp.data or []):
            if band.upper is not None and band.lower is not None:
                widths.append(band.upper - band.lower)
        
        if len(widths) < period:
            return None
        
        bb_min = min(widths[-period:])
        prev_width = widths[-1]
        
        # RS 계산
        rs_return = 0.0
        rs_resp = await self._indicator.get_relative_strength(
            code, period_days=self._cfg.rs_period_days, ohlcv_data=ohlcv
        )
        if rs_resp and rs_resp.data:
            rs_return = rs_resp.data.return_pct

        market = "KOSDAQ" if self._mapper.is_kosdaq(code) else "KOSPI"

        return OSBWatchlistItem(
            code=code, name=name, market=market,
            high_20d=high_20d, ma_20d=ma_20d, ma_50d=ma_50d,
            avg_vol_20d=avg_vol_20d, bb_width_min_20d=bb_min, prev_bb_width=prev_width,
            w52_hgpr=w52_hgpr, avg_trading_value_5d=tv_5d, market_cap=stck_llam,
            rs_return_3m=rs_return
        )

    # ── Pool A 생성 (배치) ─────────────────────────────────────────

    async def generate_pool_a(self) -> dict:
        """전체 종목 스캔 -> Pool A 생성 및 파일 저장."""
        self._logger.info({"event": "generate_pool_a_started"})
        
        # 1. 전체 종목 로드
        all_stocks = []
        for _, row in self._mapper.df.iterrows():
            code = row.get("종목코드", "")
            name = row.get("종목명", "")
            market = row.get("시장구분", "")
            if code and market in ("KOSPI", "KOSDAQ"):
                all_stocks.append((code, name, market))

        # 2. 1차 필터 (시총/거래대금)
        passed_first = []
        for chunk in _chunked(all_stocks, self._cfg.api_chunk_size):
            tasks = [self._ts.get_current_stock_price(c) for c, _, _ in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (code, name, market), resp in zip(chunk, results):
                if isinstance(resp, Exception) or not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue
                out = resp.data.get("output") if resp.data else None
                if not out: continue
                
                cap = int(out.get("stck_llam") or 0)
                val = int(out.get("acml_tr_pbmn") or 0)
                
                if self._cfg.pool_a_market_cap_min <= cap <= self._cfg.pool_a_market_cap_max:
                    if val >= self._cfg.min_avg_trading_value_5d:
                        passed_first.append((code, name, market))
            await asyncio.sleep(1.1)

        # 3. 2차 필터 (상세 분석)
        items = []
        for chunk in _chunked(passed_first, self._cfg.api_chunk_size):
            for code, name, market in chunk:
                item = await self._analyze_candidate(code, name)
                if item:
                    items.append(item)
            await asyncio.sleep(1.1)

        # 4. 스코어링 및 저장
        self._compute_rs_scores(items)
        await self._compute_profit_growth_scores(items)
        self._compute_total_scores(items)

        sort_key = lambda x: (x.total_score, self._calc_turnover_ratio(x))
        kospi = sorted([i for i in items if i.market != "KOSDAQ"], key=sort_key, reverse=True)[:self._cfg.pool_a_size_per_market]
        kosdaq = sorted([i for i in items if i.market == "KOSDAQ"], key=sort_key, reverse=True)[:self._cfg.pool_a_size_per_market]

        self._save_pool_a(kospi, kosdaq)
        
        return {
            "kospi_count": len(kospi), "kosdaq_count": len(kosdaq),
            "total_scanned": len(all_stocks), "passed_first": len(passed_first)
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

    async def _update_market_timing(self):
        for market, code in [("KOSDAQ", self._cfg.kosdaq_etf_code), ("KOSPI", self._cfg.kospi_etf_code)]:
            self._market_timing_cache[market] = await self._check_etf_ma_rising(code)

    async def _check_etf_ma_rising(self, etf_code: str) -> bool:
        period = self._cfg.market_ma_period
        days = self._cfg.market_ma_rising_days
        ohlcv = await self._ts.get_recent_daily_ohlcv(etf_code, limit=period + days + 5)
        if not ohlcv or len(ohlcv) < period + days:
            return False
        
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        ma_values = []
        for i in range(days + 1):
            end = len(closes) - days + i
            ma_values.append(sum(closes[end-period:end]) / period)
            
        return all(ma_values[j] > ma_values[j-1] for j in range(1, len(ma_values)))

    def _compute_rs_scores(self, items: List[OSBWatchlistItem]):
        if not items: return
        rets = sorted([i.rs_return_3m for i in items])
        cutoff = rets[min(int(len(rets)*(1 - self._cfg.rs_top_percentile/100)), len(rets)-1)]
        for item in items:
            item.rs_score = self._cfg.rs_score_points if item.rs_return_3m >= cutoff else 0.0

    async def _compute_profit_growth_scores(self, items: List[OSBWatchlistItem]):
        for chunk in _chunked(items, self._cfg.api_chunk_size):
            tasks = [self._ts.get_financial_ratio(i.code) for i in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for item, resp in zip(chunk, results):
                if isinstance(resp, Exception) or not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue
                growth = self._extract_op_profit_growth(resp.data)
                if growth >= self._cfg.profit_growth_threshold_pct:
                    item.profit_growth_score = self._cfg.profit_growth_score_points
            await asyncio.sleep(1.1)

    def _compute_total_scores(self, items: List[OSBWatchlistItem]):
        for item in items:
            item.total_score = item.rs_score + item.profit_growth_score

    def _save_pool_a(self, kospi, kosdaq):
        try:
            os.makedirs(os.path.dirname(self._cfg.pool_a_file), exist_ok=True)
            data = {
                "generated_date": self._tm.get_current_kst_time().strftime("%Y%m%d"),
                "kospi": [asdict(i) for i in kospi],
                "kosdaq": [asdict(i) for i in kosdaq]
            }
            with open(self._cfg.pool_a_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._logger.error(f"Failed to save Pool A: {e}")

    def _load_pool_a(self) -> List[OSBWatchlistItem]:
        if not os.path.exists(self._cfg.pool_a_file):
            return []
        try:
            with open(self._cfg.pool_a_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 날짜 체크 (오늘/어제만 유효)
            gen_date = data.get("generated_date", "")
            today = self._tm.get_current_kst_time().strftime("%Y%m%d")
            if gen_date != today and int(today) - int(gen_date) > 1: # 단순비교
                return []
            
            items = []
            for k in ["kospi", "kosdaq"]:
                for d in data.get(k, []):
                    items.append(OSBWatchlistItem(**d))
            return items
        except Exception:
            return []

    @staticmethod
    def _calc_turnover_ratio(item: OSBWatchlistItem) -> float:
        return (item.avg_trading_value_5d / item.market_cap) if item.market_cap > 0 else 0

    @staticmethod
    def _extract_op_profit_growth(data) -> float:
        # API 응답 구조에 따라 영업이익 증가율 추출 (간소화)
        try:
            target = data[0] if isinstance(data, list) and data else data
            if isinstance(target, dict):
                for k in ["bsop_prti_icdc", "sale_totl_prfi_icdc", "op_profit_growth"]:
                    if val := target.get(k): return float(val)
        except: pass
        return 0.0