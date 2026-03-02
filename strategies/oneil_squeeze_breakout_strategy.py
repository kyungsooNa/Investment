# strategies/oneil/breakout_strategy.py
from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from services.trading_service import TradingService
from core.time_manager import TimeManager
from strategies.oneil_common_types import OneilBreakoutConfig, OSBPositionState
from services.oneil_universe_service import OneilUniverseService


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
    
    [🚨 ERROR & FIX REQUIRED (v2 내 즉시 수정/추가 필요)]
    - (Buy) 매수 체결 직전 '현재가 스냅샷 체결강도(>=120%)' 검증 로직 누락
    - (Sell) 시간 손절(5일 횡보 박스권 이탈) 상세 로직 미구현
    - (Sell) 추세 이탈 청산(10MA 이탈 + 대량 거래량 동반) 로직 미구현

    [v3 예정 (TODO)]
    - 스코어링 고도화: 업종 소분류 주도 (테마 대장주) 키워드 매칭 스코어링 (+20점) 추가
    - 마켓타이밍 고도화: 코스닥/코스피 지수 직접 조회 API 연동 (ETF 프록시 대체)
    - 실시간 호가창(Websocket) 연동을 통한 초단위 고래(>=5000만원) 탐지 (현재는 부하 이슈로 보류)
    """
    STATE_FILE = os.path.join("data", "osb_position_state.json")

    def __init__(
        self,
        trading_service: TradingService,
        universe_service: OneilUniverseService,
        time_manager: TimeManager,
        config: Optional[OneilBreakoutConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._universe = universe_service
        self._tm = time_manager
        self._cfg = config or OneilBreakoutConfig()
        self._logger = logger or logging.getLogger(__name__)

        self._position_state: Dict[str, OSBPositionState] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "오닐스퀴즈돌파"

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        
        # 1. 유니버스 서비스로부터 완성된 워치리스트 획득 (캐싱됨)
        watchlist = await self._universe.get_watchlist()
        if not watchlist:
            return signals

        # 2. 장중 경과 비율 (거래량 환산용)
        market_progress = self._get_market_progress_ratio()
        if market_progress <= 0:
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

        return signals

    async def _check_breakout(self, code, item, progress) -> Optional[TradeSignal]:
        # 현재가 조회
        resp = await self._ts.get_current_stock_price(code)
        if not resp or resp.rt_cd != "0": return None

        output = resp.data.get("output") if isinstance(resp.data, dict) else None
        if not output: return None

        # Mock(dict)과 실제(객체) 모두 처리
        if isinstance(output, dict):
            current = int(output.get("stck_prpr", 0))
            vol = int(output.get("acml_vol", 0))
            pg_buy = int(output.get("pgtr_ntby_qty", 0))
            acml_tr_pbmn = int(output.get("acml_tr_pbmn", 0))
        else:
            current = int(getattr(output, "stck_prpr", 0) or 0)
            vol = int(getattr(output, "acml_vol", 0) or 0)
            pg_buy = int(getattr(output, "pgtr_ntby_qty", 0) or 0)
            acml_tr_pbmn = int(getattr(output, "acml_tr_pbmn", 0) or 0)
        
        # 1. 가격 돌파
        if current <= item.high_20d:
            return None
            
        # 2. 거래량 돌파
        # 🌟 [뻥튀기 방어 로직 추가] 🌟
        # 방어 1: 최소 진행률 5% (약 20분) 보정
        effective_progress = max(progress, 0.05)
        proj_vol = vol / effective_progress
        
        # 방어 2: 최소 절대 거래량 확보 (20일 평균의 최소 30%는 실거래되어야 함)
        if vol < (item.avg_vol_20d * 0.3):
            return None
            
        # 2. 거래량 돌파 검증
        if proj_vol < item.avg_vol_20d * self._cfg.volume_breakout_multiplier:
            return None
            
        # 3. 프로그램 수급 (기본 수량)
        if pg_buy <= self._cfg.program_net_buy_min:
            return None

        # 3-1. 프로그램 수급 (세부 조건: 거래대금/시총 비중)
        pg_buy_amt = pg_buy * current
        
        if acml_tr_pbmn > 0:
            if (pg_buy_amt / acml_tr_pbmn) * 100 < self._cfg.program_to_trade_value_pct:
                return None
        
        if item.market_cap > 0:
            if (pg_buy_amt / item.market_cap) * 100 < self._cfg.program_to_market_cap_pct:
                return None
            
        # 매수 신호 생성
        qty = self._calculate_qty(current)
        self._position_state[code] = OSBPositionState(
            entry_price=current,
            entry_date=self._tm.get_current_kst_time().strftime("%Y%m%d"),
            peak_price=current,
            breakout_level=item.high_20d
        )
        self._save_state()
        
        return TradeSignal(
            code=code, name=item.name, action="BUY", price=current, qty=qty,
            reason=f"오닐돌파: {item.high_20d}돌파, 수급{pg_buy}", strategy_name=self.name
        )

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

            resp = await self._ts.get_current_stock_price(code)
            if not resp or resp.rt_cd != "0": continue

            output = resp.data.get("output") if isinstance(resp.data, dict) else None
            if not output: continue

            if isinstance(output, dict):
                current = int(output.get("stck_prpr", 0))
            else:
                current = int(getattr(output, "stck_prpr", 0) or 0)

            if current <= 0: continue

            # 최고가 갱신
            if current > state.peak_price:
                state.peak_price = current
                self._save_state()

            pnl = (current - buy_price) / buy_price * 100
            reason = ""
            
            # 손절
            if pnl <= self._cfg.stop_loss_pct:
                reason = f"손절({pnl:.1f}%)"
            # 트레일링 스탑
            elif state.peak_price > 0:
                drop = (current - state.peak_price) / state.peak_price * 100
                if drop <= -self._cfg.trailing_stop_pct:
                    reason = f"트레일링스탑({drop:.1f}%)"
            
            # 시간 손절 (박스권 횡보)
            if not reason and await self._check_time_stop(code, state):
                reason = "시간손절(횡보)"

            if reason:
                self._position_state.pop(code, None)
                self._save_state()
                signals.append(TradeSignal(
                    code=code, name=hold.get("name", code), action="SELL", 
                    price=current, qty=1, reason=reason, strategy_name=self.name
                ))
        return signals

    async def _check_time_stop(self, code: str, state: OSBPositionState) -> bool:
        # (기존 로직 유지: N일 경과 후 박스권이면 True)
        if state.entry_date == self._tm.get_current_kst_time().strftime("%Y%m%d"):
            return False
        # ... (상세 구현 생략, 필요시 기존 코드 복사)
        return False

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