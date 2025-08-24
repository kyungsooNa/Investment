from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Literal
from core.time_manager import TimeManager

MarketCode = Literal["J", "Q"]  # J: 코스피(국내 주식), Q: 코스닥(필요시 확장)
tm = TimeManager()

# ---- 개별 파라미터 dataclass들 ----

@dataclass(frozen=True)
class SearchInfoParams:
    pdno: str
    prdt_type_cd: str  # 종목코드

    @classmethod
    def of(cls, stock_code: str, prdt_type_cd: prdt_type_cd):
        return cls(pdno=stock_code, prdt_type_cd=prdt_type_cd)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class InquirePriceParams:
    fid_cond_mrkt_div_code: str  # "J"
    fid_input_iscd: str  # 종목코드

    @classmethod
    def of(cls, stock_code: str, market: MarketCode = "J"):
        return cls(fid_cond_mrkt_div_code=market, fid_input_iscd=stock_code)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AskingPriceParams:
    fid_cond_mrkt_div_code: str
    fid_input_iscd: str

    @classmethod
    def of(cls, stock_code: str, market: MarketCode = "J"):
        return cls(fid_cond_mrkt_div_code=market, fid_input_iscd=stock_code)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TimeConcludeParams:
    fid_cond_mrkt_div_code: str
    fid_input_iscd: str

    @classmethod
    def of(cls, stock_code: str, market: MarketCode = "J"):
        return cls(fid_cond_mrkt_div_code=market, fid_input_iscd=stock_code)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DailyItemChartPriceParams:
    fid_cond_mrkt_div_code: str  # 보통 "J"
    fid_input_iscd: str  # 종목코드
    fid_input_date_1: str  # 조회 시작일
    fid_input_date_2: str  # 조회 종료일
    fid_period_div_code: str # 일/주/월/년
    fid_org_adj_prc: Literal["0", "1"]  # 수정주가 여부

    @classmethod
    def daily_itemchartprice(cls, stock_code: str, start_date: str, end_date: str, period: str, market: MarketCode = "J", adj: Literal["0", "1"] = "0"):
        return cls(
            fid_cond_mrkt_div_code=market,
            fid_input_iscd=stock_code,
            fid_input_date_1=tm.to_yyyymmdd(start_date),
            fid_input_date_2=tm.to_yyyymmdd(end_date),
            fid_period_div_code=period,
            fid_org_adj_prc=adj,
        )


    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

@dataclass(frozen=True)
class TimeItemChartPriceParams:
    fid_cond_mrkt_div_code: str  # 보통 "J"
    fid_input_iscd: str  # 종목코드
    fid_input_hour_1: str  # 입력시간
    fid_pw_data_incu_yn: str # 과거 데이터 포함 여부
    fid_etc_cls_code: str # 기타 구분 코드

    @classmethod
    def time_itemchartprice(cls, stock_code: str, fid_input_hour_1: str, fid_pw_data_incu_yn: str, fid_etc_cls_code: str, market: MarketCode = "J", adj: Literal["0", "1"] = "0"):
        return cls(
            fid_cond_mrkt_div_code=market,
            fid_input_iscd=stock_code,
            fid_input_hour_1=fid_input_hour_1,
            fid_pw_data_incu_yn=fid_pw_data_incu_yn,
            fid_etc_cls_code=fid_etc_cls_code
        )


    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

@dataclass(frozen=True)
class TimeDailyItemChartPriceParams:
    fid_cond_mrkt_div_code: str  # 보통 "J"
    fid_input_iscd: str  # 종목코드
    fid_input_hour_1: str  # 입력시간 (ex 13시 130000)
    fid_input_date_1: str  # 입력날짜 (YYYYMMDD)
    fid_pw_data_incu_yn: str # 과거 데이터 포함 여부
    fid_fake_tick_incu_yn: str # 허봉 포함 여부

    @classmethod
    def time_daily_itemchartprice(cls, stock_code: str, fid_input_hour_1: str, fid_input_date_1: str, fid_pw_data_incu_yn: str, fid_fake_tick_incu_yn: str, market: MarketCode = "J", adj: Literal["0", "1"] = "0"):
        return cls(
            fid_cond_mrkt_div_code=market,
            fid_input_iscd=stock_code,
            fid_input_hour_1=fid_input_hour_1,
            fid_input_date_1=fid_input_date_1,
            fid_pw_data_incu_yn=fid_pw_data_incu_yn,
            fid_fake_tick_incu_yn=fid_fake_tick_incu_yn
        )


    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class VolumeRankParams:
    FID_COND_MRKT_DIV_CODE: str
    FID_COND_SCR_DIV_CODE: str
    FID_INPUT_ISCD: str
    FID_DIV_CLS_CODE: str
    FID_BLNG_CLS_CODE: str
    FID_TRGT_CLS_CODE: str
    FID_TRGT_EXLS_CLS_CODE: str
    FID_INPUT_PRICE_1: str
    FID_INPUT_PRICE_2: str
    FID_VOL_CNT: str
    FID_INPUT_DATE_1: str

    @classmethod
    def default(cls, market: MarketCode = "J"):
        return cls(
            FID_COND_MRKT_DIV_CODE=market,
            FID_COND_SCR_DIV_CODE="20171",
            FID_INPUT_ISCD="0000",
            FID_DIV_CLS_CODE="0",
            FID_BLNG_CLS_CODE="0",
            FID_TRGT_CLS_CODE="0",
            FID_TRGT_EXLS_CLS_CODE="0000000000",
            FID_INPUT_PRICE_1="",
            FID_INPUT_PRICE_2="",
            FID_VOL_CNT="",
            FID_INPUT_DATE_1="",
        )

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class MarketCapScreenParams:
    fid_cond_mrkt_div_code: str  # "J"
    fid_cond_scr_div_code: str  # "20174" (시총 상위 등)
    fid_div_cls_code: str  # "0"
    fid_input_iscd: str  # "0000" (전체) 또는 업종코드
    fid_trgt_cls_code: str  # "20"
    fid_trgt_exls_cls_code: str  # "20"
    fid_input_price_1: str
    fid_input_price_2: str
    fid_vol_cnt: str

    @classmethod
    def top_market_cap(cls, market: MarketCode = "J", input_iscd: str = "0000"):
        return cls(
            fid_cond_mrkt_div_code=market,
            fid_cond_scr_div_code="20174",
            fid_div_cls_code="0",
            fid_input_iscd=input_iscd,
            fid_trgt_cls_code="",
            fid_trgt_exls_cls_code="",
            fid_input_price_1="",
            fid_input_price_2="",
            fid_vol_cnt="",
        )

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SearchStockParams:
    word: str

    @classmethod
    def of(cls, keyword: str):
        return cls(word=keyword)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ItemNewsParams:
    fid_input_iscd: str

    @classmethod
    def of(cls, stock_code: str):
        return cls(fid_input_iscd=stock_code)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ETFInfoParams:
    fid_cond_mrkt_div_code: str
    fid_input_iscd: str

    @classmethod
    def of(cls, etf_code: str, market: MarketCode = "J"):
        return cls(fid_cond_mrkt_div_code=market, fid_input_iscd=etf_code)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FluctuationParams:
    fid_rsfl_rate2: str  # 등락 비율2 ( ~비율 ) 공백이면 전체
    fid_cond_mrkt_div_code: str  # 시장구분 (주식 J)
    fid_cond_scr_div_code: str  # 조건 화면 분류 코드 (20170)
    fid_input_iscd: str  # 0000(전체) 0001(코스피) 1001(코스닥) 2001(코스피200)
    fid_rank_sort_cls_code: str  # 0:상승율 1:하락율 2:시가대비상승율 3:시가대비하락율 4:변동율
    fid_input_cnt_1: str  # 0:전체 또는 누적일수
    fid_prc_cls_code: str  # (정렬코드 0: 저가대비/종가대비, 1: 고가대비/종가대비, 기타: 0=전체)
    fid_input_price_1: str  # 가격 ~
    fid_input_price_2: str  # ~ 가격
    fid_vol_cnt: str  # 거래량 ~
    fid_trgt_cls_code: str  # 대상 (0:전체)
    fid_trgt_exls_cls_code: str  # 대상제외 (0:전체)
    fid_div_cls_code: str  # 분류 (0:전체)
    fid_rsfl_rate1: str  # 등락 비율1 (비율 ~)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    # ---- 기본 빌더: 공백/기본값 채우기 ----
    @staticmethod
    def _base(
            market: MarketCode = "J",
            input_iscd: str = "0000",
            rank_sort: Literal["0", "1", "2", "3", "4"] = "0",
            prc_cls: Optional[str] = None,
            input_cnt_1: str = "0",
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> "FluctuationParams":
        # rank_sort(정렬)에 따른 prc_cls 기본값
        # 0:상승율순 -> 0:저가대비, 1:종가대비 (기본 '1' 권장)
        # 1:하락율순 -> 0:고가대비, 1:종가대비 (기본 '1' 권장)
        # 2/3/4     -> 0:전체
        if prc_cls is None:
            if rank_sort in ("0", "1"):
                prc_cls = "1"  # 종가대비 기본
            else:
                prc_cls = "0"  # 전체

        return FluctuationParams(
            fid_rsfl_rate2=rsfl_rate2,
            fid_cond_mrkt_div_code=market,
            fid_cond_scr_div_code="20170",
            fid_input_iscd=input_iscd,
            fid_rank_sort_cls_code=rank_sort,
            fid_input_cnt_1=input_cnt_1,
            fid_prc_cls_code=prc_cls,
            fid_input_price_1=price_1,
            fid_input_price_2=price_2,
            fid_vol_cnt=vol_cnt,
            fid_trgt_cls_code=trgt_cls,
            fid_trgt_exls_cls_code=trgt_exls,
            fid_div_cls_code=div_cls,
            fid_rsfl_rate1=rsfl_rate1,
        )

    # ---- 편의 빌더들 ----
    @classmethod
    def rising(
            cls,
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # "0"(저가대비) / "1"(종가대비)
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> "FluctuationParams":
        return cls._base(
            market=market, input_iscd=input_iscd,
            rank_sort="0", prc_cls=prc_cls, input_cnt_1=input_cnt_1,
            rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
            price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
            trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls
        )

    @classmethod
    def falling(
            cls,
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # "0"(고가대비) / "1"(종가대비)
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> "FluctuationParams":
        return cls._base(
            market=market, input_iscd=input_iscd,
            rank_sort="1", prc_cls=prc_cls, input_cnt_1=input_cnt_1,
            rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
            price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
            trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls
        )

    @classmethod
    def since_open_rise(
            cls,
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # "0"(저가대비) / "1"(종가대비)
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> "FluctuationParams":
        # 2:시가대비상승율
        return cls._base(market=market, input_iscd=input_iscd, rank_sort="2", prc_cls=prc_cls, input_cnt_1=input_cnt_1,
                         rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
                         price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
                         trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls)

    @classmethod
    def since_open_fall(
            cls,
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # "0"(저가대비) / "1"(종가대비)
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> "FluctuationParams":
        # 3:시가대비하락율
        return cls._base(market=market, input_iscd=input_iscd, rank_sort="3", prc_cls=prc_cls, input_cnt_1=input_cnt_1,
                         rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
                         price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
                         trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls)

    @classmethod
    def volatility(
            cls,
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # "0"(저가대비) / "1"(종가대비)
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> "FluctuationParams":
        # 4:변동율
        return cls._base(market=market, input_iscd=input_iscd, rank_sort="4", prc_cls=prc_cls, input_cnt_1=input_cnt_1,
                         rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
                         price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
                         trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls)


@dataclass(frozen=True)
class AccountBalanceParams:
    CANO: str  # 계좌번호 앞 8자리
    ACNT_PRDT_CD: str  # 계좌상품코드 (기본: "01")
    AFHR_FLPR_YN: str = "N"  # 시간외단일가 포함여부
    FNCG_AMT_AUTO_RDPT_YN: str = "N"  # 융자금액자동상환여부
    FUND_STTL_ICLD_YN: str = "N"  # 펀드결제분포함여부
    INQR_DVSN: str = "01"  # 조회구분코드 (기본: 01)
    OFL_YN: str = "N"  # 오프라인여부
    PRCS_DVSN: str = "01"  # 처리구분코드
    UNPR_DVSN: str = "01"  # 단가구분코드
    CTX_AREA_FK100: str = ""  # 연속조회검색조건100
    CTX_AREA_NK100: str = ""  # 연속조회키100

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def create(
            cls,
            cano: str,
            acnt_prdt_cd: str = "01",
            afhr_flpr_yn: str = "N",
            fncg_amt_auto_rdpt_yn: str = "N",
            fund_sttl_icld_yn: str = "N",
            inqr_dvsn: str = "01",
            ofl_yn: str = "N",
            prcs_dvsn: str = "01",
            unpr_dvsn: str = "01",
            ctx_area_fk100: str = "",
            ctx_area_nk100: str = "",
    ) -> "AccountBalanceParams":
        return cls(
            CANO=cano,
            ACNT_PRDT_CD=acnt_prdt_cd,
            AFHR_FLPR_YN=afhr_flpr_yn,
            FNCG_AMT_AUTO_RDPT_YN=fncg_amt_auto_rdpt_yn,
            FUND_STTL_ICLD_YN=fund_sttl_icld_yn,
            INQR_DVSN=inqr_dvsn,
            OFL_YN=ofl_yn,
            PRCS_DVSN=prcs_dvsn,
            UNPR_DVSN=unpr_dvsn,
            CTX_AREA_FK100=ctx_area_fk100,
            CTX_AREA_NK100=ctx_area_nk100,
        )


@dataclass(frozen=True)
class OrderCashBody:
    CANO: str
    ACNT_PRDT_CD: str
    PDNO: str
    ORD_DVSN: str  # 00 지정가 / 01 시장가 ...
    ORD_QTY: str
    ORD_UNPR: str  # 시장가일 땐 "0" 등 규약대로

    def to_dict(self):
        return asdict(self)


# ---- 얇은 파사드: 기존 코드에서 함수 호출만으로 dict를 얻을 수 있게 ----

class Params:
    """기존 코드 변경 최소화를 위한 dict 파사드"""

    @staticmethod
    def search_info(stock_code: str, prdt_type_cd: MarketCode) -> Dict[str, str]:
        return SearchInfoParams.of(stock_code, prdt_type_cd).to_dict()

    @staticmethod
    def inquire_price(stock_code: str, market: MarketCode = "J") -> Dict[str, str]:
        return InquirePriceParams.of(stock_code, market).to_dict()

    @staticmethod
    def asking_price(stock_code: str, market: MarketCode = "J") -> Dict[str, str]:
        return AskingPriceParams.of(stock_code, market).to_dict()

    @staticmethod
    def time_conclude(stock_code: str, market: MarketCode = "J") -> Dict[str, str]:
        return TimeConcludeParams.of(stock_code, market).to_dict()

    @staticmethod
    def daily_itemchartprice(stock_code: str, start_date: str, end_date: str, period: str, market: MarketCode = "J", adj: Literal["0", "1"] = "0") -> \
            Dict[str, str]:
        return DailyItemChartPriceParams.daily_itemchartprice(stock_code, start_date, end_date, period, market, adj).to_dict()

    @staticmethod
    def time_itemchartprice(stock_code: str, input_hour: str,
                            include_past: str, etc_cls_code: str, market: MarketCode = "UN") -> dict:
        return TimeItemChartPriceParams.time_itemchartprice(stock_code, input_hour, include_past, etc_cls_code, market).to_dict()

    @staticmethod
    def time_daily_itemchartprice(stock_code: str, input_hour: str, input_date: str, include_past: str, fid_pw_data_incu_yn: str, market: MarketCode = "UN") -> \
            Dict[str, str]:
        return TimeDailyItemChartPriceParams.time_daily_itemchartprice(stock_code, input_hour, input_date, include_past, fid_pw_data_incu_yn, market).to_dict()

    @staticmethod
    def volume_rank(market: MarketCode = "J") -> Dict[str, str]:
        return VolumeRankParams.default(market).to_dict()

    @staticmethod
    def top_market_cap(market: MarketCode = "J", input_iscd: str = "0000") -> Dict[str, str]:
        return MarketCapScreenParams.top_market_cap(market, input_iscd).to_dict()

    @staticmethod
    def search_stock(keyword: str) -> Dict[str, str]:
        return SearchStockParams.of(keyword).to_dict()

    @staticmethod
    def item_news(stock_code: str) -> Dict[str, str]:
        return ItemNewsParams.of(stock_code).to_dict()

    @staticmethod
    def etf_info(etf_code: str, market: MarketCode = "J") -> Dict[str, str]:
        return ETFInfoParams.of(etf_code, market).to_dict()

    @staticmethod
    def fluctuation_rise(
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # 0/1
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> Dict[str, str]:
        return FluctuationParams.rising(
            market, input_iscd=input_iscd, input_cnt_1=input_cnt_1, prc_cls=prc_cls,
            rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
            price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
            trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls
        ).to_dict()

    @staticmethod
    def fluctuation_fall(
            market: MarketCode = "J",
            *,
            input_iscd: str = "0000",
            input_cnt_1: str = "0",
            prc_cls: Optional[str] = None,  # 0/1
            rsfl_rate1: str = "",
            rsfl_rate2: str = "",
            price_1: str = "",
            price_2: str = "",
            vol_cnt: str = "",
            trgt_cls: str = "0",
            trgt_exls: str = "0",
            div_cls: str = "0",
    ) -> Dict[str, str]:
        return FluctuationParams.falling(
            market, input_iscd=input_iscd, input_cnt_1=input_cnt_1, prc_cls=prc_cls,
            rsfl_rate1=rsfl_rate1, rsfl_rate2=rsfl_rate2,
            price_1=price_1, price_2=price_2, vol_cnt=vol_cnt,
            trgt_cls=trgt_cls, trgt_exls=trgt_exls, div_cls=div_cls
        ).to_dict()

    @staticmethod
    def account_balance(
            cano: str,
            acnt_prdt_cd: str = "01",
            afhr_flpr_yn: str = "N",
            fncg_amt_auto_rdpt_yn: str = "N",
            fund_sttl_icld_yn: str = "N",
            inqr_dvsn: str = "01",
            ofl_yn: str = "N",
            prcs_dvsn: str = "01",
            unpr_dvsn: str = "01",
            ctx_area_fk100: str = "",
            ctx_area_nk100: str = "",
    ) -> Dict[str, str]:
        return AccountBalanceParams.create(
            cano, acnt_prdt_cd,
            afhr_flpr_yn, fncg_amt_auto_rdpt_yn,
            fund_sttl_icld_yn, inqr_dvsn, ofl_yn,
            prcs_dvsn, unpr_dvsn,
            ctx_area_fk100, ctx_area_nk100
        ).to_dict()

    @staticmethod
    def order_cash_body(*, cano: str, acnt_prdt_cd: str, pdno: str,
                        ord_dvsn: str, ord_qty: str | int, ord_unpr: str | int) -> dict:
        return OrderCashBody(
            CANO=cano,
            ACNT_PRDT_CD=acnt_prdt_cd,
            PDNO=pdno,
            ORD_DVSN=ord_dvsn,
            ORD_QTY=str(ord_qty),
            ORD_UNPR=str(ord_unpr),
        ).to_dict()
