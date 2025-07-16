# common/types.py
import enum
from dataclasses import dataclass
from typing import Optional, Any, List


# API 응답 결과의 성공/실패를 나타내는 Enum
class ErrorCode(enum.Enum):
    SUCCESS = "0"
    API_ERROR = "100"       # 외부 API 호출 실패
    PARSING_ERROR = "101"   # 응답 파싱 실패
    INVALID_INPUT = "102"   # 유효성 오류
    NETWORK_ERROR = "103"   # 네트워크 오류
    MISSING_KEY  = "104"    # MISSING_KEY
    UNKNOWN_ERROR = "999"   # 기타 오류


# --- 공통적으로 사용되는 데이터 응답 구조 ---

@dataclass
class ResPriceSummary:
    symbol: str
    open: int
    current: int
    change_rate: float
    prdy_ctrt: float


@dataclass
class ResMomentumStock:
    symbol: str
    change_rate: float
    prev_volume: int
    current_volume: int


@dataclass
class ResMarketCapStockItem:
    rank: Optional[str]
    name: Optional[str]
    code: str
    current_price: Optional[str]


# --- 한국투자증권 API 특화 응답 구조 ---

# --- 한국투자증권 API 특화 응답 구조 (종목 상세정보) ---

@dataclass
class ResStockFullInfoApiOutput:
    acml_tr_pbmn: str  # 누적 거래 대금 (원)
    acml_vol: str  # 누적 거래량 (주)
    aspr_unit: str  # 호가 단위
    bps: str  # 주당순자산 (Book-value Per Share)
    bstp_kor_isnm: str  # 업종명 (예: 일반서비스)
    clpr_rang_cont_yn: str  # 종가 범위제 적용 여부
    cpfn: str  # 자본금 (억원)
    cpfn_cnnm: str  # 자본금 (단위표기 포함, 예: 468 억)
    crdt_able_yn: str  # 신용거래 가능 여부 (Y/N)
    d250_hgpr: str  # 250일 최고가
    d250_hgpr_date: str  # 250일 최고가 기록일
    d250_hgpr_vrss_prpr_rate: str  # 현재가 대비 250일 최고가 등락률 (%)
    d250_lwpr: str  # 250일 최저가
    d250_lwpr_date: str  # 250일 최저가 기록일
    d250_lwpr_vrss_prpr_rate: str  # 현재가 대비 250일 최저가 등락률 (%)
    dmrs_val: str  # 매도호가 1 (최우선)
    dmsp_val: str  # 매수호가 1 (최우선)
    dryy_hgpr_date: str  # 당해 연도 최고가 기록일
    dryy_hgpr_vrss_prpr_rate: str  # 현재가 대비 연중 최고가 등락률 (%)
    dryy_lwpr_date: str  # 당해 연도 최저가 기록일
    dryy_lwpr_vrss_prpr_rate: str  # 현재가 대비 연중 최저가 등락률 (%)
    elw_pblc_yn: str  # ELW 발행 여부
    eps: str  # 주당순이익 (Earnings Per Share)
    fcam_cnnm: str  # 액면가 (단위 포함, 예: 500 원)
    frgn_hldn_qty: str  # 외국인 보유 수량
    frgn_ntby_qty: str  # 외국인 순매매 수량
    grmn_rate_cls_code: str  # 결산월 분류코드
    hts_avls: str  # HTS 시가총액 (원)
    hts_deal_qty_unit_val: str  # HTS 거래량 단위 값
    hts_frgn_ehrt: str  # HTS 외국인 보유율 (%)
    invt_caful_yn: str  # 투자주의 종목 여부
    iscd_stat_cls_code: str  # 종목 상태 코드
    last_ssts_cntg_qty: str  # 직전 체결 수량
    lstn_stcn: str  # 상장 주식수 (주)
    mang_issu_cls_code: str  # 관리종목 구분 코드
    marg_rate: str  # 증거금율 (%)
    mrkt_warn_cls_code: str  # 시장경고종목 분류코드
    oprc_rang_cont_yn: str  # 시가 범위제 적용 여부
    ovtm_vi_cls_code: str  # 시간외 VI 발동 분류코드
    pbr: str  # 주가순자산비율 (Price to Book Ratio)
    per: str  # 주가수익비율 (Price Earnings Ratio)
    pgtr_ntby_qty: str  # 프로그램 매매 순매수 수량
    prdy_ctrt: str  # 전일 대비 등락률 (%)
    prdy_vrss: str  # 전일 대비 등락금액
    prdy_vrss_sign: str  # 전일 대비 부호 (1:상승, 2:하락, 3:보합)
    prdy_vrss_vol_rate: str  # 전일 대비 거래량 증감률 (%)
    pvt_frst_dmrs_prc: str  # 예상체결가 첫번째 매도호가
    pvt_frst_dmsp_prc: str  # 예상체결가 첫번째 매수호가
    pvt_pont_val: str  # 예상체결가 기준 예상 체결가
    pvt_scnd_dmrs_prc: str  # 예상체결가 두번째 매도호가
    pvt_scnd_dmsp_prc: str  # 예상체결가 두번째 매수호가
    rprs_mrkt_kor_name: str  # 대표시장명 (예: 코스피, 코스닥)
    rstc_wdth_prc: str  # 가격제한폭 (상하한가 차이)
    short_over_yn: str  # 공매도 과열 여부 (Y/N)
    sltr_yn: str  # 정리매매 여부 (Y/N)
    ssts_yn: str  # 정지 여부 (Y/N)
    stac_month: str  # 결산월
    stck_dryy_hgpr: str  # 당해 연도 최고가
    stck_dryy_lwpr: str  # 당해 연도 최저가
    stck_fcam: str  # 액면가
    stck_hgpr: str  # 금일 고가
    stck_llam: str  # 종가 기준 시가총액 (원)
    stck_lwpr: str  # 금일 저가
    stck_mxpr: str  # 상한가
    stck_oprc: str  # 시가
    stck_prpr: str  # 현재가
    stck_sdpr: str  # 기준가 (기준가격)
    stck_shrn_iscd: str  # 단축 종목코드
    stck_sspr: str  # 하한가
    temp_stop_yn: str  # 일시 정지 여부 (Y/N)
    vi_cls_code: str  # VI (변동성완화장치) 발동 여부
    vol_tnrt: str  # 거래 회전율 (%)
    w52_hgpr: str  # 52주 최고가
    w52_hgpr_date: str  # 52주 최고가 기록일
    w52_hgpr_vrss_prpr_ctrt: str  # 현재가 대비 52주 최고가 등락률 (%)
    w52_lwpr: str  # 52주 최저가
    w52_lwpr_date: str  # 52주 최저가 기록일
    w52_lwpr_vrss_prpr_ctrt: str  # 현재가 대비 52주 최저가 등락률 (%)
    wghn_avrg_stck_prc: str  # 가중평균주가
    whol_loan_rmnd_rate: str  # 전체 대주잔고 비율 (%)


@dataclass
class ResTopMarketCapApiItem:
    iscd: str
    mksc_shrn_iscd: str
    stck_avls: str
    data_rank: str
    hts_kor_isnm: Optional[str]
    acc_trdvol: str


@dataclass
class ResDailyChartApiItem:
    stck_bsop_date: str
    stck_oprc: str
    stck_hgpr: str
    stck_lwpr: str
    stck_clpr: str
    acml_vol: str


@dataclass
class ResAccountBalanceApiOutput:
    pdno: str
    prdt_name: str
    evlu_amt: str


@dataclass
class ResStockOrderApiOutput:
    ordno: str
    prdt_no: str


# --- 공통 응답 구조 (유지 또는 dataclass로 래핑 가능) ---

@dataclass
class ResCommonResponse:
    rt_cd: str
    msg1: str
    data: Optional[Any]
