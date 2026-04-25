# common/types.py
from datetime import datetime
from typing import Optional, Generic, TypeVar, Type, Any, Dict
from enum import Enum, auto
from pydantic import BaseModel, Field, model_validator, ConfigDict

T = TypeVar("T")


class Exchange(str, Enum):
    KRX = "KRX"
    NXT = "NXT"
    UN = "UN"     # 통합시세 (KRX+NXT, 시세 조회 전용)


# API 응답 결과의 성공/실패를 나타내는 Enum
class ErrorCode(Enum):
    SUCCESS = "0"
    API_ERROR = "100"  # 외부 API 호출 실패
    PARSING_ERROR = "101"  # 응답 파싱 실패
    INVALID_INPUT = "102"  # 유효성 오류
    NETWORK_ERROR = "103"  # 네트워크 오류
    MISSING_KEY = "104"  # MISSING_KEY
    RETRY_LIMIT = "105"  # RETRY_LIMIT
    WRONG_RET_TYPE = "106"  # WRONG_RET_TYPE
    EMPTY_VALUES = "107"  # 조회 결과 없음
    MARKET_CLOSED = "108"  # 장 마감
    KILL_SWITCH_BLOCKED = "109"  # Kill Switch 활성 상태로 주문 차단
    UNKNOWN_ERROR = "999"  # 기타 오류

    @property
    def is_retriable(self) -> bool:
        """서비스 레벨에서 재시도 가능한 오류인지 반환."""
        return self in (ErrorCode.NETWORK_ERROR, ErrorCode.RETRY_LIMIT)


# --- 전략 신호 ---
class TradeSignal(BaseModel):
    """전략에서 생성하는 표준 매수/매도 신호."""
    code: str
    name: str
    action: str  # "BUY" / "SELL"
    price: int
    qty: int = 1
    reason: str = ""
    strategy_name: str = ""
    exchange: str = "KRX"  # "KRX" 또는 "NXT"

    def to_dict(self):
        return self.model_dump()


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderState(str, Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
        }


class OrderContext(BaseModel):
    order_key: str
    stock_code: str
    side: OrderSide
    state: OrderState
    exchange: Exchange = Exchange.KRX
    price: int
    qty: int
    source: str = "default"
    attempt_count: int = 0
    filled_qty: int = 0
    remaining_qty: int = 0
    virtual_recorded_qty: int = 0
    broker_order_no: Optional[str] = None
    last_error_code: Optional[str] = None
    last_error_message: str = ""
    created_at: Optional[datetime] = None
    state_entered_at: Optional[datetime] = None
    last_stuck_alert_at: Optional[datetime] = None
    last_stuck_alert_level: str = ""
    trace_id: Optional[str] = None

    @model_validator(mode="after")
    def sync_remaining_qty(self) -> "OrderContext":
        self.remaining_qty = max(self.qty - self.filled_qty, 0)
        return self

    def to_dict(self):
        return self.model_dump()

    def can_transition_to(self, new_state: OrderState) -> bool:
        allowed_transitions = {
            OrderState.PENDING_SUBMIT: {
                OrderState.SUBMITTED,
                OrderState.CANCELED,
                OrderState.REJECTED,
            },
            OrderState.SUBMITTED: {
                OrderState.PARTIAL_FILLED,
                OrderState.FILLED,
                OrderState.CANCELED,
                OrderState.REJECTED,
            },
            OrderState.PARTIAL_FILLED: {
                OrderState.FILLED,
                OrderState.CANCELED,
            },
            OrderState.FILLED: set(),
            OrderState.CANCELED: set(),
            OrderState.REJECTED: set(),
        }
        return new_state == self.state or new_state in allowed_transitions[self.state]

    def transition(
        self,
        new_state: OrderState,
        *,
        attempt_count: Optional[int] = None,
        filled_qty: Optional[int] = None,
        virtual_recorded_qty: Optional[int] = None,
        broker_order_no: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        transition_time: Optional[datetime] = None,
        stuck_alert_at: Optional[datetime] = None,
        stuck_alert_level: Optional[str] = None,
    ) -> "OrderContext":
        if not self.can_transition_to(new_state):
            raise ValueError(f"잘못된 주문 상태 전이: {self.state} -> {new_state}")

        next_filled_qty = self.filled_qty if filled_qty is None else max(filled_qty, 0)
        next_virtual_recorded_qty = self.virtual_recorded_qty if virtual_recorded_qty is None else max(virtual_recorded_qty, 0)
        next_attempt_count = self.attempt_count if attempt_count is None else attempt_count
        next_broker_order_no = self.broker_order_no if broker_order_no is None else broker_order_no
        state_changed = new_state != self.state
        next_state_entered_at = self.state_entered_at
        if state_changed:
            next_state_entered_at = transition_time or datetime.now()

        next_last_stuck_alert_at = self.last_stuck_alert_at
        next_last_stuck_alert_level = self.last_stuck_alert_level
        if state_changed:
            next_last_stuck_alert_at = None
            next_last_stuck_alert_level = ""
        if stuck_alert_at is not None:
            next_last_stuck_alert_at = stuck_alert_at
        if stuck_alert_level is not None:
            next_last_stuck_alert_level = stuck_alert_level

        return self.model_copy(update={
            "state": new_state,
            "attempt_count": next_attempt_count,
            "filled_qty": next_filled_qty,
            "virtual_recorded_qty": min(next_virtual_recorded_qty, next_filled_qty),
            "remaining_qty": max(self.qty - next_filled_qty, 0),
            "broker_order_no": next_broker_order_no,
            "last_error_code": error_code,
            "last_error_message": error_message or "",
            "state_entered_at": next_state_entered_at,
            "last_stuck_alert_at": next_last_stuck_alert_at,
            "last_stuck_alert_level": next_last_stuck_alert_level,
        })


class OrderExecutionReport(BaseModel):
    """체결통보 WebSocket과 주문조회 polling 결과를 FSM에 적용하기 위한 공통 이벤트."""
    broker_order_no: str
    stock_code: str
    side: Optional[OrderSide] = None
    exchange: Exchange = Exchange.KRX
    event_state: OrderState = OrderState.SUBMITTED
    order_qty: Optional[int] = None
    fill_qty: int = 0
    fill_price: int = 0
    cumulative_filled_qty: Optional[int] = None
    remaining_qty: Optional[int] = None
    original_order_no: Optional[str] = None
    event_time: str = ""
    source: str = "websocket"
    message: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)

    @property
    def event_key(self) -> str:
        return (
            f"{self.source}:{self.broker_order_no}:{self.event_time}:"
            f"{self.event_state.value}:{self.fill_qty}:{self.fill_price}:{self.cumulative_filled_qty}"
        )

    def to_dict(self):
        return self.model_dump()

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            text = str(value or "").replace(",", "").strip()
            return int(float(text)) if text else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_side(value: Any) -> Optional[OrderSide]:
        side_value = str(value or "").strip().upper()
        if side_value in ("01", "1", "매도", "SELL"):
            return OrderSide.SELL
        if side_value in ("02", "2", "매수", "BUY"):
            return OrderSide.BUY
        return None

    @staticmethod
    def _parse_exchange(value: Any) -> Exchange:
        exchange_value = str(value or "").upper()
        return Exchange.NXT if exchange_value == Exchange.NXT.value else Exchange.KRX

    @classmethod
    def from_signing_notice(cls, data: dict, *, tr_id: str = "") -> "OrderExecutionReport":
        side = cls._parse_side(data.get("매도매수구분") or data.get("SELN_BYOV_CLS"))
        fill_qty = cls._to_int(data.get("체결수량") or data.get("CNTG_QTY"))
        order_qty = cls._to_int(data.get("주문수량") or data.get("ODER_QTY")) or None
        rejected = str(data.get("거부여부") or data.get("RFUS_YN") or "").upper() == "Y"
        accepted = str(data.get("접수여부") or data.get("ACPT_YN") or "").upper() == "Y"
        concluded = str(data.get("체결여부") or data.get("CNTG_YN") or "") == "2"

        if rejected:
            event_state = OrderState.REJECTED
        elif concluded and order_qty and fill_qty >= order_qty:
            event_state = OrderState.FILLED
        elif concluded and fill_qty > 0:
            event_state = OrderState.PARTIAL_FILLED
        elif accepted:
            event_state = OrderState.SUBMITTED
        else:
            event_state = OrderState.SUBMITTED

        exchange = cls._parse_exchange(data.get("주문거래소구분") or data.get("ORD_EXG_GB"))

        return cls(
            broker_order_no=str(data.get("주문번호") or data.get("ODER_NO") or "").strip(),
            original_order_no=str(data.get("원주문번호") or data.get("OODER_NO") or "").strip() or None,
            stock_code=str(data.get("주식단축종목코드") or data.get("STCK_SHRN_ISCD") or "").strip(),
            side=side,
            exchange=exchange,
            event_state=event_state,
            order_qty=order_qty,
            fill_qty=fill_qty,
            fill_price=cls._to_int(data.get("체결단가") or data.get("CNTG_UNPR")),
            event_time=str(data.get("주식체결시간") or data.get("STCK_CNTG_HOUR") or ""),
            source=f"websocket:{tr_id}" if tr_id else "websocket",
            message="거부" if rejected else ("체결" if concluded else "접수"),
            raw=data,
        )

    @classmethod
    def from_order_query(cls, data: dict, *, tr_id: str = "") -> "OrderExecutionReport":
        """주문체결내역 조회(inquire-daily-ccld) row를 공통 체결 이벤트로 변환합니다."""
        order_qty = cls._to_int(data.get("ord_qty") or data.get("ORD_QTY") or data.get("주문수량")) or None
        filled_qty = cls._to_int(data.get("tot_ccld_qty") or data.get("TOT_CCLD_QTY") or data.get("체결수량"))
        raw_remaining_qty = data.get("rmn_qty") or data.get("RMN_QTY") or data.get("잔여수량")
        remaining_qty = cls._to_int(raw_remaining_qty) if raw_remaining_qty not in (None, "") else None
        rejected_qty = cls._to_int(data.get("rjct_qty") or data.get("RJCT_QTY") or data.get("거부수량"))
        canceled_qty = cls._to_int(data.get("cncl_cfrm_qty") or data.get("CNCL_CFRM_QTY") or data.get("취소확인수량"))
        canceled = str(data.get("cncl_yn") or data.get("CNCL_YN") or "").upper() == "Y"

        if rejected_qty and filled_qty == 0 and (order_qty is None or rejected_qty >= order_qty):
            event_state = OrderState.REJECTED
        elif canceled or (
            canceled_qty
            and (
                remaining_qty == 0
                or order_qty is None
                or filled_qty + canceled_qty >= order_qty
            )
        ):
            event_state = OrderState.CANCELED
        elif order_qty and filled_qty >= order_qty:
            event_state = OrderState.FILLED
        elif remaining_qty is not None and remaining_qty == 0 and filled_qty > 0:
            event_state = OrderState.FILLED
        elif filled_qty > 0:
            event_state = OrderState.PARTIAL_FILLED
        else:
            event_state = OrderState.SUBMITTED

        event_time = str(
            data.get("ord_dt")
            or data.get("ORD_DT")
            or data.get("주문일자")
            or ""
        ) + str(data.get("ord_tmd") or data.get("ORD_TMD") or data.get("주문시각") or "")

        return cls(
            broker_order_no=str(data.get("odno") or data.get("ODNO") or data.get("주문번호") or "").strip(),
            original_order_no=str(data.get("orgn_odno") or data.get("ORGN_ODNO") or data.get("원주문번호") or "").strip() or None,
            stock_code=str(data.get("pdno") or data.get("PDNO") or data.get("종목코드") or "").strip(),
            side=cls._parse_side(data.get("sll_buy_dvsn_cd") or data.get("SLL_BUY_DVSN_CD") or data.get("매도매수구분")),
            exchange=cls._parse_exchange(data.get("excg_dvsn_cd") or data.get("EXCG_DVSN_CD") or data.get("거래소구분")),
            event_state=event_state,
            order_qty=order_qty,
            fill_qty=filled_qty,
            fill_price=cls._to_int(data.get("avg_prvs") or data.get("AVG_PRVS") or data.get("평균가")),
            cumulative_filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            event_time=event_time,
            source=f"polling:{tr_id}" if tr_id else "polling",
            message="주문조회",
            raw=data,
        )


# --- 공통적으로 사용되는 데이터 응답 구조 ---
class ResPriceSummary(BaseModel):
    symbol: str
    open: int
    current: int
    change_rate: float
    prdy_ctrt: float
    new_high_low_status: Optional[str] = None

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResMomentumStock(BaseModel):
    symbol: str
    change_rate: float
    prev_volume: int
    current_volume: int

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResMarketCapStockItem(BaseModel):
    rank: Optional[str]
    name: Optional[str]
    code: str
    current_price: Optional[str]

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


# --- 한국투자증권 API 특화 응답 구조 ---

# --- 한국투자증권 API 특화 응답 구조 (종목 상세정보) ---


class ResStockFullInfoApiOutput(BaseModel):
    acml_tr_pbmn: str = ""  # 누적 거래 대금 (원)
    acml_vol: str = ""  # 누적 거래량 (주)
    aspr_unit: str = ""  # 호가 단위
    bps: str = ""  # 주당순자산 (Book-value Per Share)
    bstp_kor_isnm: str = ""  # 업종명 (예: 일반서비스)
    clpr_rang_cont_yn: str = ""  # 종가 범위제 적용 여부
    cpfn: str = ""  # 자본금 (억원)
    cpfn_cnnm: str = ""  # 자본금 (단위표기 포함, 예: 468 억)
    crdt_able_yn: str = ""  # 신용거래 가능 여부 (Y/N)
    d250_hgpr: str = ""  # 250일 최고가
    d250_hgpr_date: str = ""  # 250일 최고가 기록일
    d250_hgpr_vrss_prpr_rate: str = ""  # 현재가 대비 250일 최고가 등락률 (%)
    d250_lwpr: str = ""  # 250일 최저가
    d250_lwpr_date: str = ""  # 250일 최저가 기록일
    d250_lwpr_vrss_prpr_rate: str = ""  # 현재가 대비 250일 최저가 등락률 (%)
    dmrs_val: str = ""  # 매도호가 1 (최우선)
    dmsp_val: str = ""  # 매수호가 1 (최우선)
    dryy_hgpr_date: str = ""  # 당해 연도 최고가 기록일
    dryy_hgpr_vrss_prpr_rate: str = ""  # 현재가 대비 연중 최고가 등락률 (%)
    dryy_lwpr_date: str = ""  # 당해 연도 최저가 기록일
    dryy_lwpr_vrss_prpr_rate: str = ""  # 현재가 대비 연중 최저가 등락률 (%)
    elw_pblc_yn: str = ""  # ELW 발행 여부
    eps: str = ""  # 주당순이익 (Earnings Per Share)
    fcam_cnnm: str = ""  # 액면가 (단위 포함, 예: 500 원)
    frgn_hldn_qty: str = ""  # 외국인 보유 수량
    frgn_ntby_qty: str = ""  # 외국인 순매매 수량
    grmn_rate_cls_code: str = ""  # 결산월 분류코드
    hts_avls: str = ""  # HTS 시가총액 (원)
    hts_deal_qty_unit_val: str = ""  # HTS 거래량 단위 값
    hts_frgn_ehrt: str = ""  # HTS 외국인 보유율 (%)
    invt_caful_yn: str = ""  # 투자주의 종목 여부
    iscd_stat_cls_code: str = ""  # 종목 상태 코드
    last_ssts_cntg_qty: str = ""  # 직전 체결 수량
    lstn_stcn: str = ""  # 상장 주식수 (주)
    mang_issu_cls_code: str = ""  # 관리종목 구분 코드
    marg_rate: str = ""  # 증거금율 (%)
    mrkt_warn_cls_code: str = ""  # 시장경고종목 분류코드
    new_hgpr_lwpr_cls_code: Optional[str] = None  # 신고가/신저가 구분 코드
    oprc_rang_cont_yn: str = ""  # 시가 범위제 적용 여부
    ovtm_vi_cls_code: str = ""  # 시간외 VI 발동 분류코드 (NXT 응답에 미포함)
    pbr: str = ""  # 주가순자산비율 (Price to Book Ratio)
    per: str = ""  # 주가수익비율 (Price Earnings Ratio)
    pgtr_ntby_qty: str = ""  # 프로그램 매매 순매수 수량
    prdy_ctrt: str = ""  # 전일 대비 등락률 (%)
    prdy_vrss: str = ""  # 전일 대비 등락금액
    prdy_vrss_sign: str = ""  # 전일 대비 부호 (1:상승, 2:하락, 3:보합)
    prdy_vrss_vol_rate: str = ""  # 전일 대비 거래량 증감률 (%)
    pvt_frst_dmrs_prc: str = ""  # 예상체결가 첫번째 매도호가
    pvt_frst_dmsp_prc: str = ""  # 예상체결가 첫번째 매수호가
    pvt_pont_val: str = ""  # 예상체결가 기준 예상 체결가
    pvt_scnd_dmrs_prc: str = ""  # 예상체결가 두번째 매도호가
    pvt_scnd_dmsp_prc: str = ""  # 예상체결가 두번째 매수호가
    rprs_mrkt_kor_name: str = ""  # 대표시장명 (예: 코스피, 코스닥)
    rstc_wdth_prc: str = ""  # 가격제한폭 (상하한가 차이)
    short_over_yn: str = ""  # 공매도 과열 여부 (Y/N)
    sltr_yn: str = ""  # 정리매매 여부 (Y/N)
    ssts_yn: str = ""  # 정지 여부 (Y/N)
    stac_month: str = ""  # 결산월 (NXT 응답에 미포함)
    stck_dryy_hgpr: str = ""  # 당해 연도 최고가
    stck_dryy_lwpr: str = ""  # 당해 연도 최저가
    stck_fcam: str = ""  # 액면가
    stck_hgpr: str  # 금일 고가
    stck_llam: str = ""  # 종가 기준 시가총액 (원)
    stck_lwpr: str  # 금일 저가
    stck_mxpr: str = ""  # 상한가
    stck_oprc: str  # 시가
    stck_prpr: str  # 현재가
    stck_sdpr: str  # 기준가 (기준가격)
    stck_shrn_iscd: str = ""  # 단축 종목코드
    stck_sspr: str = ""  # 하한가
    temp_stop_yn: str = ""  # 일시 정지 여부 (Y/N)
    vi_cls_code: str = ""  # VI (변동성완화장치) 발동 여부
    vol_tnrt: str = ""  # 거래 회전율 (%)
    w52_hgpr: str = ""  # 52주 최고가
    w52_hgpr_date: str = ""  # 52주 최고가 기록일
    w52_hgpr_vrss_prpr_ctrt: str = ""  # 현재가 대비 52주 최고가 등락률 (%)
    w52_lwpr: str = ""  # 52주 최저가
    w52_lwpr_date: str = ""  # 52주 최저가 기록일
    w52_lwpr_vrss_prpr_ctrt: str = ""  # 현재가 대비 52주 최저가 등락률 (%)
    wghn_avrg_stck_prc: str = ""  # 가중평균주가
    whol_loan_rmnd_rate: str = ""  # 전체 대주잔고 비율 (%)

    @property
    def is_new_high(self) -> bool:
        return self.new_hgpr_lwpr_cls_code in ("1", "신고가")

    @property
    def is_new_low(self) -> bool:
        return self.new_hgpr_lwpr_cls_code in ("2", "신저가")

    @property
    def new_high_low_status(self) -> str:
        if self.is_new_high:
            return "신고가"
        if self.is_new_low:
            return "신저가"
        return "-"

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict, log_missing: bool = True) -> "ResStockFullInfoApiOutput":
        return cls.model_validate(data)


class ResTopMarketCapApiItem(BaseModel):
    """
    [시가총액 상위 종목 응답 아이템]
    - 모든 수치는 API가 문자열(String)로 반환하므로 str 유지 (대형 정수/소수 정밀도 보존 목적)
    - KIS 스펙(필드/의미/길이)을 주석으로 명시
    """

    # ── 필수/핵심 필드 ─────────────────────────────────────────────────────────────
    mksc_shrn_iscd: str                  # 유가증권 단축 종목코드 (String, len<=9)          e.g. '005930'
    data_rank: str                       # 데이터 순위 (String, len<=10)                   e.g. '1'
    hts_kor_isnm: str                    # HTS 한글 종목명 (String, len<=40)               e.g. '삼성전자'
    stck_avls: str                       # 시가총액 (String, len<=18)                      e.g. '467000000000000'

    # ── 시세/등락 관련(옵셔널) ────────────────────────────────────────────────────
    stck_prpr: Optional[str] = None      # 주식 현재가 (String, len<=10)
    prdy_vrss: Optional[str] = None      # 전일 대비 (가격 차이) (String, len<=10)
    prdy_vrss_sign: Optional[str] = None # 전일 대비 부호 (String, len<=1)               e.g. '1', '2', '3' 등
    prdy_ctrt: Optional[str] = None      # 전일 대비율 (String, len<=82)                  e.g. '2.31'

    # ── 거래/상장 관련(옵셔널) ────────────────────────────────────────────────────
    acml_vol: Optional[str] = None       # 누적 거래량 (String, len<=18)
    lstn_stcn: Optional[str] = None      # 상장 주수 (String, len<=18)

    # ── 시장 비중(옵셔널) ────────────────────────────────────────────────────────
    mrkt_whol_avls_rlim: Optional[str] = None  # 시장 전체 시총 대비 비중 (String, len<=52)

    # ── 과거 코드 호환용 Alias (옵셔널) ──────────────────────────────────────────
    # 기존 일부 로직/테스트가 참조하던 필드들:
    iscd: Optional[str] = None           # (호환) 단축코드 별칭. 없으면 mksc_shrn_iscd로 채움
    acc_trdvol: Optional[str] = None     # (호환) 누적 거래량 별칭. 없으면 acml_vol로 채움

    @model_validator(mode='after')
    def sync_aliases(self) -> "ResTopMarketCapApiItem":
        # 과거 호환: iscd가 없으면 mksc_shrn_iscd로 보완
        if not self.iscd:
            self.iscd = self.mksc_shrn_iscd
        # 과거 호환: acc_trdvol <-> acml_vol 동기화
        if self.acc_trdvol and not self.acml_vol:
            self.acml_vol = self.acc_trdvol
        elif self.acml_vol and not self.acc_trdvol:
            self.acc_trdvol = self.acml_vol
        return self

    def to_dict(self):
        """Dict 직렬화(테스트/로그용)."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)

    # 선택: 원시 API payload를 받아 alias 키까지 정규화하고 생성하고 싶다면 사용
    @classmethod
    def from_api(cls, payload: dict) -> "ResTopMarketCapApiItem":
        """
        - 공식 스펙 키 우선(mksc_shrn_iscd, data_rank, hts_kor_isnm, stck_avls, stck_prpr, prdy_vrss, prdy_vrss_sign,
          prdy_ctrt, acml_vol, lstn_stcn, mrkt_whol_avls_rlim)
        - 구키(alias)도 허용(iscd -> mksc_shrn_iscd, acc_trdvol -> acml_vol)
        """
        norm = dict(payload) if payload else {}

        # 단축코드 alias
        if "mksc_shrn_iscd" not in norm and "iscd" in norm:
            norm["mksc_shrn_iscd"] = norm.get("iscd")

        # 거래량 alias
        if "acml_vol" not in norm and "acc_trdvol" in norm:
            norm["acml_vol"] = norm.get("acc_trdvol")

        # Pydantic 생성
        return cls.model_validate(norm)


class ResDailyChartApiItem(BaseModel):
    stck_bsop_date: str
    stck_oprc: str
    stck_hgpr: str
    stck_lwpr: str
    stck_clpr: str
    acml_vol: str = ""

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResAccountBalanceApiOutput(BaseModel):
    pdno: str
    prdt_name: str
    evlu_amt: str

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResStockOrderApiOutput(BaseModel):
    ordno: str
    prdt_no: str

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


# 종목 요약 정보 응답 구조 (상승률 기반 필터링용 등)
class ResBasicStockInfo(BaseModel):
    code: str
    name: str
    # open_price: int
    current_price: int
    change_rate: float
    prdy_ctrt: float

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResFluctuation(BaseModel):
    stck_shrn_iscd: str   #주식 단축 종목코드
    data_rank: str = ""   #데이터 순위
    hts_kor_isnm: str = ""   #HTS 한글 종목명
    stck_prpr: str   #주식 현재가
    prdy_vrss: str = ""   #전일 대비
    prdy_vrss_sign: str = ""   #전일 대비 부호
    prdy_ctrt: str = ""   #전일 대비율
    acml_vol: str = ""   #누적 거래량
    stck_hgpr: str = ""   #주식 최고가
    hgpr_hour: str = ""   #최고가 시간
    acml_hgpr_date: str = ""   #누적 최고가 일자
    stck_lwpr: str = ""   #주식 최저가
    lwpr_hour: str = ""   #최저가 시간
    acml_lwpr_date: str = ""   #누적 최저가 일자
    lwpr_vrss_prpr_rate: str = ""   #최저가 대비 현재가 비율
    dsgt_date_clpr_vrss_prpr_rate: str = ""   #지정 일자 종가 대비 현재가 비
    cnnt_ascn_dynu: str = ""   #연속 상승 일수
    hgpr_vrss_prpr_rate: str = ""   #최고가 대비 현재가 비율
    cnnt_down_dynu: str = ""   #연속 하락 일수
    oprc_vrss_prpr_sign: str = ""   #시가2 대비 현재가 부호
    oprc_vrss_prpr: str = ""   #시가2 대비 현재가
    oprc_vrss_prpr_rate: str = ""   #시가2 대비 현재가 비율
    prd_rsfl: str = ""   #기간 등락
    prd_rsfl_rate: str = ""   #기간 등락 비율

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict) -> "ResFluctuation":
        # Pydantic handles missing fields if Optional, or we can use validator
        return cls.model_validate(data)
    

class ResBollingerBand(BaseModel):
    code: str
    date: str
    close: Optional[float]
    middle: Optional[float]
    upper: Optional[float]
    lower: Optional[float]

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResRSI(BaseModel):
    code: str
    date: str
    close: float
    rsi: float

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResMovingAverage(BaseModel):
    code: str
    date: str
    close: float
    ma: Optional[float]

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


class ResRelativeStrength(BaseModel):
    """N일 수익률 (상대강도 원시값)."""
    code: str
    date: str
    return_pct: float  # N일 수익률 (%)

    def to_dict(self):
        return self.model_dump()


class ResRSRating(BaseModel):
    """RS Rating (IBD/오닐 방식 1~99 백분위 순위)."""
    code: str
    trade_date: str
    rs_rating: int          # 1~99 백분위 점수
    weighted_rs: float      # 오닐 가중 RS 원시값 (정규화 전)

    def to_dict(self):
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)


# --- 공통 응답 구조 (유지 또는 dataclass로 래핑 가능) ---

class ResCommonResponse(BaseModel, Generic[T]):
    rt_cd: str
    msg1: str
    data: Optional[T] = None

    def to_dict(self):
        data_serialized = None
        if hasattr(self.data, 'to_dict') and callable(getattr(self.data, 'to_dict')):
            # data 필드 자체가 to_dict를 가진 객체인 경우
            data_serialized = self.data.to_dict()
        elif isinstance(self.data, BaseModel):
            data_serialized = self.data.model_dump()
        elif isinstance(self.data, (list, tuple)):
            # data 필드가 리스트/튜플인 경우, 내부 항목들도 재귀적으로 직렬화
            data_serialized = []
            for item in self.data:
                if hasattr(item, 'to_dict') and callable(getattr(item, 'to_dict')):
                    data_serialized.append(item.to_dict())
                elif isinstance(item, BaseModel):
                    data_serialized.append(item.model_dump())
                elif isinstance(item, (list, tuple, dict)): # 중첩된 리스트/딕셔너리도 처리
                    # 여기서 재귀적으로 self._serialize 같은 함수를 사용하면 좋지만,
                    # types.py에서는 cache_store._serialize를 직접 호출할 수 없으므로,
                    # to_dict를 가진 객체만 처리하거나, 더 일반적인 재귀 직렬화 로직을 이 안에 구현해야 함.
                    # 일단은 to_dict를 가진 객체만 처리하도록 간소화합니다.
                    data_serialized.append(item) # to_dict 없는 객체는 그대로
                else:
                    data_serialized.append(item)
        elif isinstance(self.data, dict):
            # data 필드가 딕셔너리인 경우, 내부 값들도 재귀적으로 직렬화
            data_serialized = {}
            for k, v in self.data.items():
                if hasattr(v, 'to_dict') and callable(getattr(v, 'to_dict')):
                    data_serialized[k] = v.to_dict()
                elif isinstance(v, BaseModel):
                    data_serialized[k] = v.model_dump()
                elif isinstance(v, (list, tuple, dict)): # 중첩된 리스트/딕셔너리도 처리
                    data_serialized[k] = v # to_dict 없는 객체는 그대로
                else:
                    data_serialized[k] = v
        else:
            # 그 외 기본 JSON 직렬화 가능 타입은 그대로 반환
            data_serialized = self.data

        return {
            "rt_cd": self.rt_cd,
            "msg1": self.msg1,
            "data": data_serialized
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls.model_validate(data)
