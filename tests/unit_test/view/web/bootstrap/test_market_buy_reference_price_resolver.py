"""service_container의 시장가 매수 기준가격 resolver 단위 테스트."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, ResCommonResponse
from view.web.bootstrap.service_container import (
    _extract_int_field,
    _resolve_market_buy_reference_price,
)


SUCCESS = ErrorCode.SUCCESS.value


# --- _extract_int_field ---

def test_extract_int_field_from_flat_dict():
    assert _extract_int_field({"askp1": "70000"}, "askp1") == 70000


def test_extract_int_field_from_output_subdict():
    data = {"output": {"stck_prpr": "12,500"}}
    assert _extract_int_field(data, "stck_prpr") == 12500


def test_extract_int_field_returns_none_for_empty_or_zero():
    assert _extract_int_field({"askp1": ""}, "askp1") is None
    assert _extract_int_field({"askp1": "0"}, "askp1") is None
    assert _extract_int_field({}, "askp1") is None
    assert _extract_int_field(None, "askp1") is None


def test_extract_int_field_tries_multiple_keys_in_order():
    data = {"output1": {"매도호가1": "8500"}}
    assert _extract_int_field(data, "askp1", "매도호가1") == 8500


def test_extract_int_field_handles_list_payload():
    data = [{"askp1": "9100"}]
    assert _extract_int_field(data, "askp1") == 9100


def test_extract_int_field_skips_non_numeric():
    data = {"askp1": "N/A"}
    assert _extract_int_field(data, "askp1") is None


# --- _resolve_market_buy_reference_price ---

def _resp(rt_cd: str, data):
    return ResCommonResponse(rt_cd=rt_cd, msg1="", data=data)


@pytest.mark.asyncio
async def test_resolver_returns_first_ask_when_asking_price_succeeds():
    broker = MagicMock()
    broker.get_asking_price = AsyncMock(return_value=_resp(SUCCESS, {"askp1": "70000"}))
    broker.get_current_price = AsyncMock()

    result = await _resolve_market_buy_reference_price(
        broker, MagicMock(), "005930", Exchange.KRX
    )

    assert result == 70000
    broker.get_current_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolver_falls_back_to_current_price_when_ask_missing():
    broker = MagicMock()
    broker.get_asking_price = AsyncMock(return_value=_resp(SUCCESS, {"askp1": "0"}))
    broker.get_current_price = AsyncMock(
        return_value=_resp(SUCCESS, {"output": {"stck_prpr": "68500"}})
    )

    result = await _resolve_market_buy_reference_price(
        broker, MagicMock(), "005930", Exchange.KRX
    )

    assert result == 68500


@pytest.mark.asyncio
async def test_resolver_falls_back_when_asking_price_fails_rt_cd():
    broker = MagicMock()
    broker.get_asking_price = AsyncMock(return_value=_resp("1", None))
    broker.get_current_price = AsyncMock(
        return_value=_resp(SUCCESS, {"stck_prpr": "55000"})
    )

    result = await _resolve_market_buy_reference_price(
        broker, MagicMock(), "005930", Exchange.KRX
    )

    assert result == 55000


@pytest.mark.asyncio
async def test_resolver_falls_back_when_asking_price_raises():
    broker = MagicMock()
    broker.get_asking_price = AsyncMock(side_effect=RuntimeError("API down"))
    broker.get_current_price = AsyncMock(
        return_value=_resp(SUCCESS, {"stck_prpr": "42000"})
    )
    logger = MagicMock()

    result = await _resolve_market_buy_reference_price(
        broker, logger, "005930", Exchange.KRX
    )

    assert result == 42000
    logger.warning.assert_called()


@pytest.mark.asyncio
async def test_resolver_returns_none_when_both_sources_fail():
    broker = MagicMock()
    broker.get_asking_price = AsyncMock(return_value=_resp(SUCCESS, {"askp1": ""}))
    broker.get_current_price = AsyncMock(return_value=_resp("1", None))

    result = await _resolve_market_buy_reference_price(
        broker, MagicMock(), "005930", Exchange.KRX
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolver_returns_none_when_broker_is_none():
    result = await _resolve_market_buy_reference_price(
        None, MagicMock(), "005930", Exchange.KRX
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolver_returns_none_when_both_raise():
    broker = MagicMock()
    broker.get_asking_price = AsyncMock(side_effect=RuntimeError("ask down"))
    broker.get_current_price = AsyncMock(side_effect=RuntimeError("curr down"))

    result = await _resolve_market_buy_reference_price(
        broker, MagicMock(), "005930", Exchange.KRX
    )

    assert result is None
