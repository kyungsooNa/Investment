# strategies/oneil/breakout_strategy.py
from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from services.stock_query_service import StockQueryService
from core.time_manager import TimeManager
from strategies.oneil_common_types import OneilBreakoutConfig, OSBPositionState
from services.oneil_universe_service import OneilUniverseService
from core.logger import get_strategy_logger


class OneilSqueezeBreakoutStrategy(LiveStrategy):
    """오닐식 스퀴즈 주도주 돌파매매 (O'Neil Squeeze Breakout).

    핵심: 시장 주도주 중 볼린저 밴드가 극도로 수축(스퀴즈)된 종목이
        거래량을 동반하며 20일 최고가를 돌파할 때 매수.
        프로그램 순매수 필터(2중 스마트 머니)로 기관 수급 확인.
    
    특징:
      - 유니버스 관리(종목 발굴)는 OneilUniverseService에 위임.
      - 이 클래스는 '언제 살까(돌파)'와 '언제 팔까(청산)'에만 집중.
      
    [v1 범위]
    - 유니버스: get_top_trading_value_stocks() → 기본 필터(거래대금/52주고가/정배열)
    - 매수/매도 뼈대 구축 및 스케줄러 연동 완료

    [v2 완료 사항]
    - 아키텍처: OneilUniverseService 완전 분리 및 메모리 캐싱 (API 중복 호출 방지)
    - 유니버스: Pool A(장 마감 후 배치) / Pool B(장중 3중 그물망 실시간 발굴) 병합
    - 스코어링: RS(3개월 상대강도 상위10% → +30점), 영업이익 25% 이상 증가 → +20점 적용
    - 마켓타이밍: ETF 프록시(KODEX 200/코스닥150) 20일선 3일 연속 우상향 로직 적용
    
    [v3 예정 (TODO)]
    - 스코어링 고도화: 업종 소분류 주도 (테마 대장주) 키워드 매칭 스코어링 (+20점) 추가
    - 마켓타이밍 고도화: 코스닥/코스피 지수 직접 조회 API 연동 (ETF 프록시 대체)
    - 실시간 호가창(Websocket) 연동을 통한 초단위 고래(>=5000만원) 탐지 (현재는 부하 이슈로 보류)
    """
    STATE_FILE = os.path.join("data", "osb_position_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        universe_service: OneilUniverseService,
        time_manager: TimeManager,
        config: Optional[OneilBreakoutConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._sqs = stock_query_service
        self._universe = universe_service
        self._tm = time_manager
        self._cfg = config or OneilBreakoutConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("OneilSqueezeBreakout", sub_dir="oneil")

        self._position_state: Dict[str, OSBPositionState] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "오닐스퀴즈돌파"

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "scan_started", "strategy_name": self.name})
        
        # 1. 유니버스 서비스로부터 완성된 워치리스트 획득 (캐싱됨)
        watchlist = await self._universe.get_watchlist()
        if not watchlist:
            self._logger.info({"event": "scan_skipped", "reason": "Watchlist is empty"})
            return signals

        self._logger.info({"event": "scan_with_watchlist", "count": len(watchlist)})

        # 2. 장중 경과 비율 (거래량 환산용)
        market_progress = self._get_market_progress_ratio()
        if market_progress <= 0:
            self._logger.info({"event": "scan_skipped", "reason": "Market not open or just started"})
            return signals

        # 3. 종목별 돌파 체크
        for code, item in watchlist.items():
            if code in self._position_state:
                continue

            # 마켓 타이밍 체크 (서비스 위임)
            if not await self._universe.is_market_timing_ok(item.market):
                continue

            # 스퀴즈 조건 (이미 유니버스에서 걸러졌지만, 전일 대비 BB폭 확인 등 추가 체크 가능)
            # 여기서는 유니버스 필터를 통과했으므로, 실시간 돌파 여부만 확인
            
            try:
                signal = await self._check_breakout(code, item, market_progress)
                if signal:
                    signals.append(signal)
            except Exception as e:
                self._logger.error(f"Scan error {code}: {e}")

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def _check_breakout(self, code, item, progress) -> Optional[TradeSignal]:
        # 1. 기본 시세 및 프로그램 수급 조회
        resp = await self._sqs.get_current_price(code)
        if not resp or resp.rt_cd != "0": return None

        out = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not out: return None

        if isinstance(out, dict):
            current = int(out.get("stck_prpr", 0))
            vol = int(out.get("acml_vol", 0))
            pg_buy = int(out.get("pgtr_ntby_qty", 0))
            trade_value = int(out.get("acml_tr_pbmn", 0))
        else:
            current = int(getattr(out, "stck_prpr", 0) or 0)
            vol = int(getattr(out, "acml_vol", 0) or 0)
            pg_buy = int(getattr(out, "pgtr_ntby_qty", 0) or 0)
            trade_value = int(getattr(out, "acml_tr_pbmn", 0) or 0)

        # 🚨 [관문 1] 가격 돌파
        if current <= item.high_20d:
            return None
            
        # 🚨 [관문 2] 거래량 돌파 (+ 뻥튀기 방어)
        effective_progress = max(progress, 0.05) # 장 초반 최소 5% 진행 보장
        proj_vol = vol / effective_progress
        
        if vol < (item.avg_vol_20d * 0.3): # 최소 절대 거래량 (평소 20일 평균의 30%) 미달 시 가짜 돌파
            return None
        if proj_vol < item.avg_vol_20d * self._cfg.volume_breakout_multiplier:
            return None
            
        # 🚨 [관문 3] 스마트 머니(프로그램 수급) 상세 필터
        if pg_buy <= self._cfg.program_net_buy_min:
            return None
            
        pg_buy_amount = pg_buy * current # 프로그램 순매수 금액 (추정)
        
        # 3-1. 거래대금의 10% 이상 개입했는가?
        if trade_value > 0 and (pg_buy_amount / trade_value * 100) < self._cfg.program_to_trade_value_pct:
            return None
            
        # 3-2. 시가총액의 0.5% 이상 개입했는가?
        if item.market_cap > 0 and (pg_buy_amount / item.market_cap * 100) < self._cfg.program_to_market_cap_pct:
            return None

        # 🌟 [최종 관문] 매수 직전 체결강도 스냅샷 (>=120%) 🌟
        # 이 관문까지 살아서 내려왔다면 조건이 완벽하게 맞은 상태입니다.
        # 매수 버튼을 누르기 직전, '주식현재가 체결(inquire-ccnl)' API를 1회 쏴서 체결강도를 확인합니다.
        cgld_val = 0.0
        try:
            ccnl_resp = await self._sqs.get_stock_conclusion(code)
            if ccnl_resp and ccnl_resp.rt_cd == "0":
                ccnl_output = ccnl_resp.data.get("output") if isinstance(ccnl_resp.data, dict) else None
                if ccnl_output and isinstance(ccnl_output, list) and len(ccnl_output) > 0:
                    # output은 체결 내역 배열 → 첫 번째(최신) 체결의 당일 체결강도 사용
                    val = ccnl_output[0].get("tday_rltv")
                    cgld_val = float(val) if val else 0.0
        except Exception as e:
            self._logger.warning({"event": "cgld_check_failed", "code": code, "error": str(e)})
            # 실패 시 안전을 위해 매수 보류하거나, 정책에 따라 통과시킬 수 있음. 여기서는 보류(None)
            return None

        if cgld_val < 120.0:
            self._logger.debug({"event": "breakout_rejected", "code": code, "reason": "low_execution_strength", "cgld": cgld_val})
            return None

        # ========= 모든 관문 통과! 매수 시그널 생성 =========
        qty = self._calculate_qty(current)
        self._position_state[code] = OSBPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            breakout_level=item.high_20d
        )
        self._save_state()
        
        reason_msg = f"오닐돌파(체결강도 {cgld_val:.1f}%, PG매수 {pg_buy_amount//100_000_000}억)"
        
        self._logger.info({
            "event": "buy_signal_generated",
            "code": code,
            "name": item.name,
            "price": current,
            "reason": reason_msg
        })
        
        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name
        )

    async def _check_trend_break(self, code: str, current_price: int, current_vol: int) -> tuple[bool, str]:
        """추세 이탈 검사 (10일선 붕괴 + 대량 거래량 동반)."""
        period = self._cfg.trend_exit_ma_period  # 10일
        
        # 1. 10일 MA와 20일 평균 거래량을 계산하기 위해 20일치 데이터 1회 조회
        ohlcv = await self._sqs.get_recent_daily_ohlcv(code, limit=max(period, 20))
        if not ohlcv or len(ohlcv) < period:
            return False, ""
            
        closes = [r.get("close", 0) for r in ohlcv if r.get("close")]
        volumes = [r.get("volume", 0) for r in ohlcv if r.get("volume")]
        
        # 2. 10일 이동평균선 계산
        ma_10d = sum(closes[-period:]) / period
        
        # 🚨 가격 조건: 현재가가 10일선을 깼는가? (안 깼으면 안전하므로 바로 리턴)
        if current_price >= ma_10d:
            return False, ""
            
        # 3. 거래량 조건 검증 (현재가가 10일선을 깬 상태에서만 계산)
        avg_vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        
        progress = self._get_market_progress_ratio()
        effective_progress = max(progress, 0.05) # 뻥튀기 방어 (최소 5% 진행 보장)
        proj_vol = current_vol / effective_progress
        
        # 🚨 거래량 조건: 장중 환산(예상) 거래량이 평소 20일 평균보다 많은가? (기관 매도 징후)
        if proj_vol > avg_vol_20d:
            reason = f"추세이탈(10MA {ma_10d:,.0f} 붕괴+대량거래)"
            self._logger.warning({
                "event": "trend_break_triggered",
                "code": code,
                "price": current_price,
                "ma_10d": round(ma_10d, 0),
                "proj_vol": int(proj_vol),
                "avg_vol": int(avg_vol_20d)
            })
            return True, reason
            
        return False, ""

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals = []
        for hold in holdings:
            code = hold.get("code")
            buy_price = hold.get("buy_price")
            if not code or not buy_price: continue
            
            state = self._position_state.get(code)
            if not state:
                state = OSBPositionState(buy_price, "", buy_price, buy_price)
                self._position_state[code] = state

            resp = await self._sqs.get_current_price(code)
            if not resp or resp.rt_cd != "0": continue

            output = resp.data.get("output") if isinstance(resp.data, dict) else None
            if not output: continue

            if isinstance(output, dict):
                current = int(output.get("stck_prpr", 0))
                current_vol = int(output.get("acml_vol", 0))
            else:
                current = int(getattr(output, "stck_prpr", 0) or 0)
                current_vol = int(getattr(output, "acml_vol", 0) or 0)

            if current <= 0: continue

            # 최고가 갱신
            if current > state.peak_price:
                state.peak_price = current
                self._save_state()

            pnl = (current - buy_price) / buy_price * 100
            reason = ""
            
            # 1. 손절
            if pnl <= self._cfg.stop_loss_pct:
                reason = f"손절({pnl:.1f}%)"
            # 2. 트레일링 스탑
            elif state.peak_price > 0:
                drop = (current - state.peak_price) / state.peak_price * 100
                if drop <= -self._cfg.trailing_stop_pct:
                    reason = f"트레일링스탑({drop:.1f}%)"
            
            # 3. 시간 손절 (박스권 횡보)
            if not reason and await self._check_time_stop(code, state, current):
                reason = f"시간손절({self._cfg.time_stop_days}일 횡보)"

            # 🌟 4. [신규 추가] 추세 이탈 (10MA 하향 + 대량 거래량)
            if not reason:
                is_break, break_reason = await self._check_trend_break(code, current, current_vol)
                if is_break:
                    reason = break_reason

            # 매도 시그널 생성
            if reason:
                self._position_state.pop(code, None)
                self._save_state()
                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL", 
                    price=current, qty=1, reason=reason, strategy_name=self.name
                ))
                
        return signals

    async def _check_time_stop(self, code: str, state: OSBPositionState, current_price: int) -> bool:
        """시간 손절 조건 체크.
        
        조건:
          1. 진입 후 N거래일(time_stop_days) 경과
          2. 현재가가 진입가 대비 박스권(time_stop_box_range_pct) 이내 횡보
          3. 진입 후 시세 분출 이력(peak_price 급등)이 없어야 함
        """
        if not state.entry_date or state.entry_price <= 0:
            return False
            
        # 🌟 최적화: 당일 진입한 종목은 굳이 API를 쏠 필요 없이 즉시 리턴 (API TPS 절약)
        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if state.entry_date.replace("-", "") == today_str:
            return False
        
        # 1. 거래일 수 계산 (OHLCV 조회)
        ohlcv = await self._sqs.get_recent_daily_ohlcv(code, limit=self._cfg.time_stop_days + 20)
        if not ohlcv:
            return False
            
        trading_days = 0
        safe_entry_date = state.entry_date.replace("-", "")
        
        # 🌟 버그 수정: == 대신 >= 를 사용하여 하이픈 제거 및 진입일 이후 데이터 필터링
        for candle in ohlcv:
            date_str = str(candle.get('date', '')).replace("-", "")
            if date_str > safe_entry_date: # 진입일 '다음 날'부터 1일로 카운트
                trading_days += 1
                
        # 설정된 거래일이 안 지났으면 패스
        if trading_days < self._cfg.time_stop_days:
            return False
            
        # 2. 횡보 또는 하락 조건 확인 (현재가가 박스권 상단 이상으로 치고 나가지 못했는가?)
        pnl_pct = (current_price - state.entry_price) / state.entry_price * 100
        
        # 🌟 버그 수정: abs() 제거. 2% 이상 '상승'한 게 아니라면 다 잘라버림 (하락 포함)
        if pnl_pct > self._cfg.time_stop_box_range_pct:
            return False
            
        # 3. '찍고 내려온 놈' 제외 (최고가가 진입가 대비 크게 오르지 않았어야 함)
        peak_pnl_pct = (state.peak_price - state.entry_price) / state.entry_price * 100
        if peak_pnl_pct > (self._cfg.time_stop_box_range_pct * 2.5):
            return False

        self._logger.info({
            "event": "time_stop_triggered",
            "code": code,
            "trading_days": trading_days,
            "pnl_pct": round(pnl_pct, 2)
        })
        return True

    def _calculate_qty(self, price: int) -> int:
        if price <= 0: return 1
        budget = self._cfg.total_portfolio_krw * (self._cfg.position_size_pct / 100)
        return max(int(budget / price), 1)

    def _get_market_progress_ratio(self) -> float:
        now = self._tm.get_current_kst_time()
        open_t = self._tm.get_market_open_time()
        close_t = self._tm.get_market_close_time()
        total = (close_t - open_t).total_seconds()
        elapsed = (now - open_t).total_seconds()
        return min(elapsed / total, 1.0) if total > 0 else 0.0

    def _load_state(self):
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._position_state[k] = OSBPositionState(**v)
            except: pass

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            data = {k: asdict(v) for k, v in self._position_state.items()}
            with open(self.STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except: pass