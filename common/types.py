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

@dataclass
class ResStockFullInfoApiOutput:
    stck_prpr: str
    stck_oprc: str
    hts_kor_isnm: str
    stck_prpr_smkl_amt: str
    prdy_vrss: str
    prdy_ctrt: str
    stck_hgpr: str
    stck_lwpr: str
    prdy_vol_rate: str
    acml_vol: str
    acml_tr_pbmn: str
    # 기타 필드 필요 시 추가


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
