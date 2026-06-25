# strategies/inverse_etf_regime_strategy.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.market_regime_service import MarketRegimeService
from core.market_clock import MarketClock
from core.logger import get_strategy_logger
from strategies.inverse_etf_regime_types import (
    InverseEtfRegimeConfig,
    InverseEtfPositionState,
)
from utils.strategy_state_io import StrategyStateIO
from utils.transaction_cost_utils import TransactionCostUtils
from utils.atomic_json import write_json_atomic

_BEAR = "bear"


class InverseEtfRegimeStrategy(LiveStrategy):
    """레짐 게이트 인버스 ETF 슬리브 (R-2 비상관 엣지).

    진입 (scan):
      - 레짐 게이트: MarketRegimeService 가 regime_market 을 'bear'(hard_decline)로 분류
      - 추세 확인: 인버스 ETF 현재가 > trend_ma_period MA (당일봉 제외)
      - 단일 종목·에피소드 1회 (position_state 보유 중이면 미진입)

    청산 (check_exits), 우선순위:
      1. 레짐 해제: regime_market 이 bear 에서 이탈 → 즉시 청산(헤지 종료)
      2. 하드 스탑: 진입가 대비 net -5%
      3. 트레일링 스톱: 보유 중 고점 대비 -8%

    long-only 7전략이 죽는 하락 구간에서만 켜지므로 구조적으로 음의 상관을 갖는다.
    실행은 일봉 셋업 + REST 가격으로 WS 틱에 비의존(ETF 무틱 블로커 우회).
    """
    STATE_FILE = os.path.join("data", "inverse_etf_regime_state.json")

    def __init__(
        self,
        stock_query_service: StockQueryService,
        market_regime_service: MarketRegimeService,
        indicator_service: IndicatorService,
        market_clock: MarketClock,
        config: Optional[InverseEtfRegimeConfig] = None,
        logger: Optional[logging.Logger] = None,
        state_file: Optional[str] = None,
    ):
        self._sqs = stock_query_service
        self._regime = market_regime_service
        self._indicator = indicator_service
        self._tm = market_clock
        self._cfg = config or InverseEtfRegimeConfig()
        if logger:
            self._logger = logger
        else:
            self._logger = get_strategy_logger("InverseEtfRegime", sub_dir="regime")

        self._position_state: Dict[str, InverseEtfPositionState] = {}
        self._cooldown: Dict[str, str] = {}  # code → unblock_date (YYYYMMDD)
        if state_file is not None:
            self.STATE_FILE = str(state_file)
        self._load_state()

    @property
    def name(self) -> str:
        return "인버스ETF레짐"

    @property
    def strategy_id(self) -> str:
        return "inverse_etf_regime"

    # ── scan ────────────────────────────────────────────────────────

    async def scan(self) -> List[TradeSignal]:
        self._logger.info({"event": "scan_started", "strategy_name": self.name})
        code = self._cfg.inverse_etf_code
        name = self._cfg.inverse_etf_name

        # 단일 종목·에피소드 1회: 이미 보유 중이면 미진입
        if code in self._position_state:
            self._logger.info({"event": "scan_skipped", "reason": "already_holding", "code": code})
            return []

        today_str = self._tm.get_current_kst_time().strftime("%Y%m%d")
        if today_str < self._cooldown.get(code, ""):
            self._logger.info({"event": "scan_skipped", "reason": "cooldown", "code": code})
            return []

        # 1) 레짐 게이트 — bear 가 아니면 미진입 (R-2 디코릴레이션 핵심)
        regime = await self._regime.classify(self._cfg.regime_market, logger=self._logger)
        if regime.regime_label != _BEAR:
            self._logger.info({
                "event": "entry_rejected", "code": code, "reason": "regime_not_bear",
                "regime_label": regime.regime_label,
            })
            return []

        # 2) 추세 확인 — 인버스 ETF 현재가 > MA (당일 미확정 봉 제외)
        ma_resp = await self._indicator.get_moving_average(
            code, period=self._cfg.trend_ma_period, candle_type="D", exclude_today=True
        )
        ma = self._latest_ma(ma_resp)
        if ma is None:
            self._logger.info({"event": "entry_rejected", "code": code, "reason": "ma_unavailable"})
            return []

        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            self._logger.info({"event": "entry_rejected", "code": code, "reason": "invalid_current_price"})
            return []
        if current <= ma:
            self._logger.info({
                "event": "entry_rejected", "code": code, "reason": "below_trend_ma",
                "current": current, "ma": round(ma, 2),
            })
            return []

        qty = self._calculate_qty(current)
        if qty <= 0:
            self._logger.info({"event": "entry_rejected", "code": code, "reason": "zero_qty"})
            return []

        # 포지션 등록 (고점 = 진입가)
        self._position_state[code] = InverseEtfPositionState(
            entry_price=current,
            entry_date=today_str,
            peak_price=current,
        )
        self._save_state()

        stop_loss_price = current * (1 + self._cfg.hard_stop_pct / 100)
        reason_msg = (
            f"베어 레짐({regime.trend_status}) + 추세확인(현재가 {current} > {self._cfg.trend_ma_period}MA "
            f"{ma:.0f}) 진입"
        )
        self._logger.info({
            "event": "entry_signal_generated", "code": code, "name": name,
            "current": current, "qty": qty, "regime": regime.regime_label,
        })
        return [TradeSignal(
            code=code, name=name, action="BUY", price=current, qty=qty,
            reason=reason_msg, strategy_name=self.name,
            entry_reason="inverse_etf_bear_regime",
            invalidation_price=round(stop_loss_price, 2),
            stop_loss_price=round(stop_loss_price, 2),
            trailing_rule=f"peak_drawdown_{abs(self._cfg.trailing_stop_pct):.0f}pct",
            confidence=0.5,
            required_data=["market_regime", "moving_average", "current_price"],
        )]

    # ── check_exits ─────────────────────────────────────────────────

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        if not holdings:
            return signals

        # 레짐은 종목 무관·일 1회 분류이므로 한 번만 조회
        regime = await self._regime.classify(self._cfg.regime_market, logger=self._logger)
        regime_is_bear = regime.regime_label == _BEAR

        state_dirty = False
        for hold in holdings:
            try:
                result = await self._check_single_exit(hold, regime_is_bear)
            except Exception as e:  # noqa: BLE001 - 단일 종목 실패가 전체 청산을 막지 않도록
                self._logger.error(f"check_exits error: {e}")
                continue
            if result is None:
                continue
            sig, dirty = result
            if sig is not None:
                signals.append(sig)
            if dirty:
                state_dirty = True

        if state_dirty:
            self._save_state()
        return signals

    async def _check_single_exit(
        self, hold: dict, regime_is_bear: bool
    ) -> Optional[Tuple[Optional[TradeSignal], bool]]:
        """단일 보유 종목 청산 조건 검사. (signal, state_dirty) 반환."""
        code = hold.get("code")
        if not code:
            return None

        cp_resp = await self._sqs.get_current_price(code, caller=self.name)
        current = self._extract_current_price(cp_resp)
        if current <= 0:
            return None

        buy_price = int(hold.get("buy_price", 0) or 0)
        if buy_price <= 0:
            buy_price = current

        # 고점 갱신 (트레일링 기준). state 가 없으면 매수가/현재가 기준으로 초기화.
        state = self._position_state.get(code)
        state_dirty = False
        if state is None:
            state = InverseEtfPositionState(
                entry_price=buy_price,
                entry_date=hold.get("buy_date", "") or "",
                peak_price=max(buy_price, current),
            )
            self._position_state[code] = state
            state_dirty = True
        if current > state.peak_price:
            state.peak_price = current
            state_dirty = True

        pnl_pct = TransactionCostUtils.net_return_pct(buy_price, current)
        peak_drawdown_pct = (current - state.peak_price) / state.peak_price * 100 if state.peak_price else 0.0
        reason: Optional[str] = None
        is_stop_loss = False

        # 1) 레짐 해제: bear 이탈 → 헤지 종료
        if not regime_is_bear:
            reason = "레짐 해제 청산: 하락추세 종료"
        # 2) 하드 스탑: 진입가 대비 net 손절
        elif pnl_pct <= self._cfg.hard_stop_pct:
            reason = f"하드 스탑 손절: PnL(net) {pnl_pct:.2f}% ≤ {self._cfg.hard_stop_pct}%"
            is_stop_loss = True
        # 3) 트레일링 스톱: 고점 대비 하락
        elif peak_drawdown_pct <= self._cfg.trailing_stop_pct:
            reason = (
                f"트레일링 스톱: 고점 {state.peak_price} 대비 {peak_drawdown_pct:.2f}% "
                f"≤ {self._cfg.trailing_stop_pct}%"
            )

        if not reason:
            return (None, state_dirty)

        holding_qty = int(hold.get("qty", 1) or 1)
        self._logger.info({
            "event": "exit_signal_generated", "code": code, "name": hold.get("name", code),
            "reason": reason, "pnl_pct": round(pnl_pct, 2), "pnl_basis": "net",
        })

        self._position_state.pop(code, None)
        state_dirty = True
        if is_stop_loss:
            unblock = (date.today() + timedelta(days=self._cfg.cooldown_days)).strftime("%Y%m%d")
            self._cooldown[code] = unblock

        return (
            TradeSignal(
                code=code, name=hold.get("name", code), action="SELL",
                price=current, qty=holding_qty, reason=reason, strategy_name=self.name,
            ),
            state_dirty,
        )

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_current_price(resp) -> int:
        """get_current_price 응답에서 현재가 정수값을 추출. 실패 시 0."""
        if not resp or getattr(resp, "rt_cd", "") != "0":
            return 0
        data = getattr(resp, "data", None)
        out = data.get("output") if isinstance(data, dict) else data
        if not out:
            return 0
        try:
            if isinstance(out, dict):
                return int(out.get("stck_prpr", 0) or 0)
            return int(getattr(out, "stck_prpr", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _latest_ma(resp) -> Optional[float]:
        """get_moving_average 응답에서 최신 MA 값을 추출. 실패 시 None."""
        if isinstance(resp, Exception):
            return None
        if not resp or getattr(resp, "rt_cd", "") != "0" or not getattr(resp, "data", None):
            return None
        try:
            ma = resp.data[-1].get("ma")
            return float(ma) if ma is not None else None
        except (TypeError, ValueError, IndexError):
            return None

    def _calculate_qty(self, price: int) -> int:
        """Fixed-fractional 사이징(소형 슬롯). use_fixed_qty=True 면 1주."""
        if price <= 0:
            return self._cfg.min_qty
        if self._cfg.use_fixed_qty:
            return self._cfg.min_qty
        budget = self._cfg.total_portfolio_krw * (self._cfg.position_size_pct / 100.0)
        return max(int(budget / price), self._cfg.min_qty)

    # ── state persistence ───────────────────────────────────────────

    def _apply_loaded_state(self, data) -> None:
        if not isinstance(data, dict):
            return
        positions = data.get("positions", {}) or {}
        cooldown = data.get("cooldown", {}) or {}
        for k, v in positions.items():
            self._position_state[k] = InverseEtfPositionState(**v)
        self._cooldown = dict(cooldown)

    def _load_state(self):
        """sync entry. 이벤트 루프 안이면 async 태스크로, 밖이면 동기 경로."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if not os.path.exists(self.STATE_FILE):
                return
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._apply_loaded_state(data)
            except Exception as e:
                self._logger.error(f"Failed to load state for {self.name}: {e}")
            return
        asyncio.create_task(self._load_state_async())

    async def _load_state_async(self):
        try:
            data = await StrategyStateIO.load(self.STATE_FILE)
        except Exception as e:
            self._logger.error(f"Failed to load state async for {self.name}: {e}")
            return
        if data is None:
            return
        self._apply_loaded_state(data)

    async def load_state(self):
        """초기화 직후 scan 전에 호출. _load_state_async() 를 명시적으로 await."""
        await self._load_state_async()

    def _payload(self) -> dict:
        return {
            "positions": {k: asdict(v) for k, v in self._position_state.items()},
            "cooldown": dict(self._cooldown),
        }

    def _save_state(self):
        """sync entry. 이벤트 루프 안이면 schedule_save, 밖이면 동기 atomic write."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                write_json_atomic(self.STATE_FILE, self._payload(), indent=2, ensure_ascii=False)
            except Exception as e:
                self._logger.error(f"Failed to save state for {self.name}: {e}")
            return
        StrategyStateIO.schedule_save(self.STATE_FILE, self._payload())

    async def _save_state_async(self):
        try:
            await StrategyStateIO.save_atomic(self.STATE_FILE, self._payload())
        except Exception as e:
            self._logger.error(f"Failed to save state async for {self.name}: {e}")
