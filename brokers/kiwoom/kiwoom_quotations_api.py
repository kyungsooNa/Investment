"""키움 REST 시세 API (Phase 4).

키움 원본 응답을 서비스 계층이 그대로 소비할 수 있도록 공통 도메인 타입
(`ResStockFullInfoApiOutput` / `ResDailyChartApiItem`)으로 변환한다 — 계획서
"서비스 계층에는 `ResCommonResponse`/공통 도메인 타입으로 변환해 넘긴다" 방침.

주의: 키움 시세 값에는 부호 접두가 붙는다(`+70100`, `-69500`). OHLC/현재가는
절대값으로 정규화하고, 전일대비처럼 부호 자체가 의미인 필드만 원본을 보존한다.
"""
from typing import Optional

from pydantic import ValidationError

from brokers.kiwoom.kiwoom_api_base import KiwoomApiBase
from brokers.kiwoom import kiwoom_params_provider as Params
from brokers.kiwoom.kiwoom_url_provider import KiwoomApiId, path_for
from common.types import (
    ErrorCode,
    Exchange,
    ResCommonResponse,
    ResDailyChartApiItem,
    ResStockFullInfoApiOutput,
)

# 공식 스펙 ka10080 `tic_scope` 허용값.
_TICK_SCOPES = {"1", "3", "5", "10", "15", "30", "45", "60"}
_PERIOD_CODES = {"D", "W", "M", "Y"}

# 응답 배열 키 (공식 스펙 response.body)
_DAILY_ROWS_KEY = "stk_dt_pole_chart_qry"
_MINUTE_ROWS_KEY = "stk_min_pole_chart_qry"

# 키움 ka10001 -> ResStockFullInfoApiOutput. 값은 절대값 정규화 대상.
_PRICE_FIELDS = {
    "cur_prc": "stck_prpr",
    "open_pric": "stck_oprc",
    "high_pric": "stck_hgpr",
    "low_pric": "stck_lwpr",
    "250hgst": "d250_hgpr",
    "250lwst": "d250_lwpr",
    "upl_pric": "stck_mxpr",
    "lst_pric": "stck_llam",
    "base_pric": "stck_sdpr",
}

# 부호·문자열을 그대로 옮기는 필드.
# 종목명(`stk_nm`)·ROE 는 `ResStockFullInfoApiOutput` 에 대응 필드가 없어 매핑하지 않는다.
# 종목명은 KIS 경로와 마찬가지로 `StockCodeRepository` 가 담당한다. pydantic 이 모르는
# 키를 조용히 버리므로, 대응 필드 없는 매핑을 넣으면 값이 소리 없이 사라진다.
_PLAIN_FIELDS = {
    "pred_pre": "prdy_vrss",
    "pre_sig": "prdy_vrss_sign",
    "flu_rt": "prdy_ctrt",
    "trde_qty": "acml_vol",
    "mac": "hts_avls",
    "per": "per",
    "eps": "eps",
    "pbr": "pbr",
    "bps": "bps",
    "cap": "cpfn",
    "flo_stk": "lstn_stcn",
    "for_exh_rt": "hts_frgn_ehrt",
}


# 기본값이 없는 모델 필드 — 키움 응답에 대응 값이 없어도 채워 넣어야 한다.
_REQUIRED_OUTPUT_FIELDS = tuple(
    name for name, field in ResStockFullInfoApiOutput.model_fields.items() if field.is_required()
)


def _strip_sign(value) -> str:
    """`+70100` / `-69500` 같은 부호 접두를 떼고 숫자 문자열만 남긴다."""
    text = str(value if value is not None else "").strip()
    if text.startswith(("+", "-")):
        return text[1:]
    return text


class KiwoomQuotationsApi(KiwoomApiBase):
    """키움 시세 조회 API."""

    def _to_full_info(self, payload: dict) -> Optional[ResStockFullInfoApiOutput]:
        mapped = {}
        for src, dst in _PRICE_FIELDS.items():
            if payload.get(src) not in (None, ""):
                mapped[dst] = _strip_sign(payload[src])
        for src, dst in _PLAIN_FIELDS.items():
            if payload.get(src) not in (None, ""):
                mapped[dst] = str(payload[src]).strip()

        if not mapped.get("stck_prpr"):
            return None

        # 모델 필수 필드는 키움 응답에 없을 수 있다(장 전 기준가 미제공 등).
        # 빈 문자열로 채워 나머지 매핑이 통째로 버려지지 않게 한다.
        for required in _REQUIRED_OUTPUT_FIELDS:
            mapped.setdefault(required, "")

        try:
            return ResStockFullInfoApiOutput(**mapped)
        except ValidationError as e:
            self._logger.error(f"키움 종목 정보 변환 실패: {e}")
            return None

    async def _call(self, api_id: str, body: dict) -> ResCommonResponse:
        return await self.call_api("POST", path_for(api_id), api_id=api_id, data=body)

    async def get_stock_info_by_code(self, stock_code: str,
                                     exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """종목 기본 정보를 조회한다 (ka10001). data 에 ResStockFullInfoApiOutput."""
        response = await self._call(KiwoomApiId.STOCK_INFO, Params.stock_info(stock_code, exchange))
        if response.rt_cd != ErrorCode.SUCCESS.value:
            return response

        info = self._to_full_info(response.data if isinstance(response.data, dict) else {})
        if info is None:
            msg = f"{stock_code} 키움 종목 정보 응답에 현재가가 없습니다."
            self._logger.warning(msg)
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=msg, data=None)

        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="종목 정보 조회 성공", data=info)

    async def get_current_price(self, stock_code: str,
                                exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """현재가를 조회한다 (ka10001).

        키움에는 KIS `inquire-price` 에 대응하는 별도 API 가 없어 기본정보로 대신한다.
        data 형태는 KIS 계약과 같게 `{'output': ResStockFullInfoApiOutput}` 로 맞춘다.
        """
        response = await self.get_stock_info_by_code(stock_code, exchange)
        if response.rt_cd != ErrorCode.SUCCESS.value:
            return response

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="현재가 조회 성공",
            data={"output": response.data},
        )

    def _to_chart_items(self, rows, date_key: str):
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                items.append(ResDailyChartApiItem(
                    stck_bsop_date=str(row[date_key]),
                    stck_oprc=_strip_sign(row.get("open_pric")),
                    stck_hgpr=_strip_sign(row.get("high_pric")),
                    stck_lwpr=_strip_sign(row.get("low_pric")),
                    stck_clpr=_strip_sign(row.get("cur_prc")),
                    acml_vol=_strip_sign(row.get("trde_qty")),
                ))
            except (KeyError, ValidationError) as e:
                self._logger.warning(f"키움 차트 행 변환 실패(스킵): {e}, row={row}")
        return items

    def _chart_response(self, response: ResCommonResponse, rows_key: str, date_key: str,
                        stock_code: str) -> ResCommonResponse:
        if response.rt_cd != ErrorCode.SUCCESS.value:
            return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1=response.msg1, data=[])

        payload = response.data if isinstance(response.data, dict) else {}
        rows = payload.get(rows_key) or []
        items = self._to_chart_items(rows, date_key)

        if not items:
            msg = f"차트 데이터가 비어있음 (stock_code: {stock_code})"
            self._logger.warning(msg)
            return ResCommonResponse(rt_cd=ErrorCode.MISSING_KEY.value, msg1=msg, data=[])

        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=items)

    async def inquire_daily_itemchartprice(self, stock_code: str,
                                           start_date: str = "",
                                           end_date: str = "",
                                           fid_period_div_code: str = "D",
                                           exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """일봉 차트를 조회한다 (ka10081).

        키움 일봉은 `base_dt` 기준 과거 방향으로 내려주므로 조회 종료일을 기준일자로 쓴다.
        주/월/년봉은 별도 api-id 라 Phase 4 범위 밖 — 명시적으로 거절한다.
        """
        if fid_period_div_code not in _PERIOD_CODES:
            msg = f"지원하지 않는 fid_period_div_code: {fid_period_div_code}"
            self._logger.error(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=[])
        if fid_period_div_code != "D":
            msg = f"키움 시세 1차 범위는 일봉(D)만 지원합니다: {fid_period_div_code}"
            self._logger.error(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=[])

        body = Params.daily_chart(stock_code, end_date or start_date, exchange)
        response = await self._call(KiwoomApiId.DAILY_CHART, body)
        return self._chart_response(response, _DAILY_ROWS_KEY, "dt", stock_code)

    async def get_intraday_minutes(self, stock_code: str, tick_scope: str = "1",
                                   base_date: str = "",
                                   exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """분봉 차트를 조회한다 (ka10080). 일자 대신 체결시간(`cntr_tm`)이 키다."""
        if str(tick_scope) not in _TICK_SCOPES:
            msg = f"지원하지 않는 분봉 틱범위: {tick_scope} (허용: {sorted(_TICK_SCOPES, key=int)})"
            self._logger.error(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=[])

        body = Params.minute_chart(stock_code, tick_scope, base_date, exchange)
        response = await self._call(KiwoomApiId.MINUTE_CHART, body)
        return self._chart_response(response, _MINUTE_ROWS_KEY, "cntr_tm", stock_code)
