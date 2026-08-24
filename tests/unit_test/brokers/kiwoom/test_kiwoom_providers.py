"""키움 REST 시세 API 의 request body / endpoint 경로 조립 테스트."""
import pytest

from brokers.kiwoom import kiwoom_params_provider as params
from brokers.kiwoom.kiwoom_url_provider import KiwoomApiId, path_for
from common.types import Exchange


@pytest.mark.parametrize(
    "exchange, expected",
    [
        (Exchange.KRX, "039490"),
        (Exchange.NXT, "039490_NX"),
        (Exchange.UN, "039490"),  # 키움에 통합 개념이 없어 KRX 로 취급한다
    ],
)
def test_stock_code_appends_exchange_suffix(exchange, expected):
    assert params.stock_code("039490", exchange) == expected


def test_stock_code_defaults_to_krx():
    assert params.stock_code("039490") == "039490"


def test_stock_info_body_carries_suffixed_code():
    assert params.stock_info("039490", Exchange.NXT) == {"stk_cd": "039490_NX"}


@pytest.mark.parametrize("adjusted, expected", [(True, "1"), (False, "0")])
def test_daily_chart_body_flags_price_adjustment(adjusted, expected):
    body = params.daily_chart("039490", "20260821", adjusted=adjusted)

    assert body == {
        "stk_cd": "039490",
        "base_dt": "20260821",
        "upd_stkpc_tp": expected,
    }


def test_minute_chart_body_omits_base_date_when_blank():
    body = params.minute_chart("039490", 1)

    assert body == {"stk_cd": "039490", "tic_scope": "1", "upd_stkpc_tp": "1"}
    assert "base_dt" not in body


def test_minute_chart_body_includes_base_date_when_given():
    body = params.minute_chart("039490", "5", base_date="20260821",
                               exchange=Exchange.NXT, adjusted=False)

    assert body == {
        "stk_cd": "039490_NX",
        "tic_scope": "5",
        "upd_stkpc_tp": "0",
        "base_dt": "20260821",
    }


@pytest.mark.parametrize(
    "api_id, expected",
    [
        (KiwoomApiId.STOCK_INFO, "/api/dostk/stkinfo"),
        (KiwoomApiId.DAILY_CHART, "/api/dostk/chart"),
        (KiwoomApiId.MINUTE_CHART, "/api/dostk/chart"),
        (KiwoomApiId.ACCOUNT_BALANCE, "/api/dostk/acnt"),
        (KiwoomApiId.ACCOUNT_DEPOSIT, "/api/dostk/acnt"),
        (KiwoomApiId.ORDER_BUY, "/api/dostk/ordr"),
        (KiwoomApiId.ORDER_SELL, "/api/dostk/ordr"),
        (KiwoomApiId.ORDER_CANCEL, "/api/dostk/ordr"),
    ],
)
def test_path_for_known_api_ids(api_id, expected):
    assert path_for(api_id) == expected


def test_path_for_unknown_api_id_raises_with_the_id_in_message():
    with pytest.raises(ValueError, match="ka99999"):
        path_for("ka99999")
