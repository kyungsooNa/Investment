from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Literal, Callable

# ===============================
# 거래량 돌파 전략 설정 클래스
# ===============================
@dataclass
class VolumeBreakoutConfig:
    """거래량 돌파 전략 및 백테스트용 설정"""
    trigger_pct: float = 10.0          # 시가 대비 +10% 도달 시 매수 트리거
    entry_push_pct: float = 2.0        # 신호 발생 후 추가 상승폭 (라이브 전략용)
    trailing_stop_pct: float = 8.0     # 고가 대비 -8% 하락 시 익절
    stop_loss_pct: float = 8.0         # 시가 대비 +8%로 하락 시 손절
    avg_vol_lookback_days: int = 20    # 평균 거래량 계산 기간 (거래일 기준 약 1개월)
    avg_vol_multiplier: float = 2.0    # 평균 거래량 대비 최소 배수 (≥2배)
    session: Literal["REGULAR", "EXTENDED"] = "REGULAR"  # 거래 세션 구분

# ===============================
# 거래량 돌파 전략 클래스
# ===============================
class VolumeBreakoutStrategy:
    """
    거래량 돌파 전략 및 단일일자 분봉 백테스트 지원:
      - 시가 대비 +10% 도달 시 매수
      - 이후 +16% 도달 시 익절, +8%로 하락 시 손절
      - 둘 다 도달하지 않으면 장 마감가로 청산
    """

    def __init__(
        self,
        *,
        stock_query_service: Any,
        time_manager: Any,
        logger: Optional[Any] = None,
        config: Optional[VolumeBreakoutConfig] = None,
    ) -> None:
        self.svc = stock_query_service   # 분봉 데이터를 가져오는 서비스
        self.time_manager = time_manager # 시간 포맷 변환 등 유틸
        self.log = logger
        self.cfg = config or VolumeBreakoutConfig()

    # -------------------------
    # 내부 유틸 함수
    # -------------------------
    @staticmethod
    def _get_first_available(d: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
        """여러 키 중 첫 번째 유효한 값을 반환"""
        for k in keys:
            v = d.get(k)
            if v is not None and v != "-":
                return v
        return default

    def _sort_key(self, r: Dict[str, Any]) -> tuple:
        """분봉 데이터를 날짜+시간 순으로 정렬하기 위한 키"""
        d = str(self._get_first_available(r, ["stck_bsop_date", "bsop_date", "date"], ""))
        t = str(self._get_first_available(r, ["stck_cntg_hour", "cntg_hour", "time"], ""))
        return d, self.time_manager.to_hhmmss(t)

    # -------------------------
    # 공개 메서드: 분봉 백테스트
    # -------------------------
    async def backtest_open_threshold_intraday(
        self,
        stock_code: str,
        *,
        date_ymd: Optional[str] = None,
        session: Optional[Literal["REGULAR", "EXTENDED"]] = None,
        trigger_pct: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        sl_pct: Optional[float] = None,
        price_getter: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None,
    ) -> Dict[str, Any]:
        """하루치 분봉 데이터를 이용한 단일일자 백테스트 수행"""
        session = session or self.cfg.session
        trigger = self.cfg.trigger_pct if trigger_pct is None else trigger_pct
        ts_pct = self.cfg.trailing_stop_pct if trailing_stop_pct is None else trailing_stop_pct
        sl = self.cfg.stop_loss_pct if sl_pct is None else sl_pct

        # 1) 분봉 데이터 로드
        rows: List[Dict[str, Any]] = await self.svc.get_day_intraday_minutes_list(
            stock_code=stock_code,
            date_ymd=date_ymd,
            session=session,
        )
        day_label = date_ymd or self.time_manager.get_current_kst_time().strftime("%Y%m%d")
        if not rows:
            return {"ok": False, "message": "분봉 데이터 없음", "stock_code": stock_code, "date": day_label, "trades": []}

        rows = sorted(rows, key=self._sort_key)

        # 2) 시가 설정 (첫 분봉의 시가 사용, 없으면 종가/가격 사용)
        def default_price_getter(r: Dict[str, Any]) -> Optional[float]:
            v = self._get_first_available(r, ["stck_prpr", "prpr", "close", "price"])
            return float(v) if v not in (None, "", "-") else None

        pg = price_getter or default_price_getter
        open0_raw = self._get_first_available(rows[0], ["stck_oprc", "oprc", "open"]) or \
                    self._get_first_available(rows[0], ["stck_prpr", "prpr", "close", "price"])
        try:
            open0 = float(open0_raw)
        except Exception:
            return {"ok": False, "message": f"시가 파싱 실패(open0={open0_raw!r})", "stock_code": stock_code, "date": day_label, "trades": []}

        # 3) 매수 트리거 찾기 (+trigger_pct 도달 시점)
        entry_idx = None
        entry_px = None
        for i, r in enumerate(rows):
            p = pg(r)
            if p is None:
                continue
            change = (p / open0 - 1.0) * 100.0
            if change >= trigger:
                entry_idx, entry_px = i, p
                break

        if entry_idx is None:
            return {"ok": True, "message": f"트리거 {trigger}% 미발생", "stock_code": stock_code, "date": day_label, "trades": [], "equity": 1.0}

        # 4) 익절/손절 조건 확인
        exit_idx = None
        exit_px = None
        outcome = "close_exit"
        curr_high = entry_px
        for j in range(entry_idx + 1, len(rows)):
            p = pg(rows[j])
            if p is None:
                continue
            curr_high = max(curr_high, p)
            drop_from_high = (p / curr_high - 1.0) * 100.0
            if drop_from_high <= -ts_pct:
                exit_idx, exit_px, outcome = j, p, "trailing_stop"
                break
            change = (p / open0 - 1.0) * 100.0
            if change <= sl:
                exit_idx, exit_px, outcome = j, p, "stop_loss"
                break

        if exit_idx is None:
            last_price = pg(rows[-1])
            if last_price is None:
                return {"ok": False, "message": "종가 가격 파싱 실패", "stock_code": stock_code, "date": day_label, "trades": []}
            exit_idx, exit_px = len(rows) - 1, last_price

        ret = (exit_px / entry_px) - 1.0

        def fmt_ts(row: Dict[str, Any]) -> str:
            d = str(self._get_first_available(row, ["stck_bsop_date", "bsop_date", "date"], ""))
            t = str(self._get_first_available(row, ["stck_cntg_hour", "cntg_hour", "time"], ""))
            return f"{d} {self.time_manager.to_hhmmss(t)}"

        trade = {
            "entry_time": fmt_ts(rows[entry_idx]),
            "entry_px": float(entry_px),
            "exit_time": fmt_ts(rows[exit_idx]),
            "exit_px": float(exit_px),
            "outcome": outcome,
            "ret": ret,
            "ret_pct": round(ret * 100.0, 3),
            "open0": float(open0),
            "trigger_pct": float(trigger),
            "trailing_stop_pct": float(ts_pct),
            "sl_pct": float(sl),
        }

        return {"ok": True, "message": "success", "stock_code": stock_code, "date": day_label, "equity": 1.0 + ret, "trades": [trade]}
