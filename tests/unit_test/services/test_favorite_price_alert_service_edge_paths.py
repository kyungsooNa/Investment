"""FavoritePriceAlertService 의 방어 분기·부호 보정 경로 테스트.

기존 테스트가 정상 알림 흐름을 다루므로, 여기서는 조기 반환(빈 코드/알림기 부재/
임계치 0), 상태 파일 입출력 실패, KIS 부호 형식 보정처럼 알림이 나가지 *않는*
경로를 채운다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.favorite_repository import MARKET_DOMESTIC, MARKET_OVERSEAS_US
from services.favorite_price_alert_service import FavoritePriceAlertService


def _build(**kwargs):
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=kwargs.pop("codes", ["005930"]))
    notifications = kwargs.pop("notifications", MagicMock())
    if notifications is not None:
        notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(
        repo, notifications, kwargs.pop("stock_codes", None),
        logger=kwargs.pop("logger", MagicMock()), **kwargs,
    )
    return svc, repo, notifications


# --- 관심종목 추가/삭제 ------------------------------------------------------

@pytest.mark.asyncio
async def test_blank_code_is_ignored_by_add_and_remove():
    svc, repo, _ = _build()

    await svc.add_favorite("   ")
    await svc.remove_favorite("")

    repo.get_all.assert_not_awaited()
    assert svc._favorite_codes == set()


@pytest.mark.asyncio
async def test_remove_favorite_drops_every_per_code_alert_state():
    svc, _, _ = _build(today_provider=lambda: "20260821")
    svc._state_date = "20260821"
    svc._favorite_codes = {"005930"}
    svc._highest_positive_alert_bucket = {"005930": 2}
    svc._lowest_negative_alert_bucket = {"005930": -1}
    svc._upper_limit_alerted_codes = {"005930"}

    await svc.remove_favorite("005930")

    assert svc._favorite_codes == set()
    assert svc._highest_positive_alert_bucket == {}
    assert svc._lowest_negative_alert_bucket == {}
    assert svc._upper_limit_alerted_codes == set()


# --- handle_price_tick 조기 반환 ---------------------------------------------

@pytest.mark.asyncio
async def test_tick_without_notification_service_is_dropped():
    svc, repo, _ = _build(notifications=None)

    assert await svc.handle_price_tick("005930", price="1", rate="9") is False
    repo.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_with_blank_code_is_dropped():
    svc, repo, _ = _build()

    assert await svc.handle_price_tick("  ", price="1", rate="9") is False
    repo.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_positive_threshold_step_disables_alerts():
    svc, repo, _ = _build(threshold_step_pct=0)

    assert await svc.handle_price_tick("005930", price="1", rate="9") is False
    repo.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_unparsable_rate_is_dropped():
    svc, _, notifications = _build()

    assert await svc.handle_price_tick("005930", price="75000", rate="없음") is False
    notifications.emit.assert_not_awaited()


# --- 상한가 상태 전이 ---------------------------------------------------------

@pytest.mark.asyncio
async def test_upper_limit_alert_fires_once_and_repeats_are_dropped():
    svc, _, notifications = _build()

    assert await svc.handle_price_tick(
        "005930", price="91000", rate="29.9", is_upper_limit=True
    ) is True
    assert await svc.handle_price_tick(
        "005930", price="91000", rate="29.9", is_upper_limit=True
    ) is False
    assert notifications.emit.await_count == 1


@pytest.mark.asyncio
async def test_falling_off_the_upper_limit_clears_the_alerted_flag():
    svc, _, _ = _build(today_provider=lambda: "20260821")
    svc._state_date = "20260821"
    svc._favorite_codes = {"005930"}
    svc._upper_limit_alerted_codes = {"005930"}

    await svc.handle_price_tick("005930", price="80000", rate="1.0")

    assert svc._upper_limit_alerted_codes == set()


@pytest.mark.parametrize(
    "market, sign, is_upper_limit, rate, expected",
    [
        (MARKET_OVERSEAS_US, "1", True, 99.0, False),  # 미국장은 상한가 제도가 없다
        (MARKET_DOMESTIC, None, True, 1.0, True),
        (MARKET_DOMESTIC, "1", False, 1.0, True),
        (MARKET_DOMESTIC, "2", False, 29.6, True),
        (MARKET_DOMESTIC, "2", False, 10.0, False),
    ],
)
def test_upper_limit_detection(market, sign, is_upper_limit, rate, expected):
    svc, _, _ = _build(market=market)

    assert svc._is_upper_limit(rate, sign=sign, is_upper_limit=is_upper_limit) is expected


# --- 관심종목 목록/상태 파일 실패 --------------------------------------------

@pytest.mark.asyncio
async def test_favorite_lookup_failure_is_logged_and_leaves_the_cache_untouched():
    svc, repo, _ = _build()
    repo.get_all = AsyncMock(side_effect=RuntimeError("db down"))

    assert await svc.handle_price_tick("005930", price="75000", rate="9.0") is False
    assert svc._favorite_codes == set()
    svc._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_state_load_failure_is_logged_and_leaves_state_empty(mocker):
    mocker.patch(
        "services.favorite_price_alert_service.StrategyStateIO.load",
        AsyncMock(side_effect=OSError("깨진 파일")),
    )
    svc, _, _ = _build(state_file="/tmp/alert-state.json", today_provider=lambda: "20260821")

    await svc._load_alert_state_for_today()

    assert svc._highest_positive_alert_bucket == {}
    svc._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_state_save_failure_is_logged(mocker):
    mocker.patch(
        "services.favorite_price_alert_service.StrategyStateIO.save_atomic",
        AsyncMock(side_effect=OSError("디스크 가득")),
    )
    svc, _, _ = _build(state_file="/tmp/alert-state.json")
    svc._state_date = "20260821"

    await svc._save_alert_state()

    svc._logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, "문자열", {"date": "20260820"}])
async def test_state_from_another_day_or_shape_is_ignored(mocker, payload):
    mocker.patch(
        "services.favorite_price_alert_service.StrategyStateIO.load",
        AsyncMock(return_value=payload),
    )
    svc, _, _ = _build(state_file="/tmp/alert-state.json", today_provider=lambda: "20260821")

    await svc._load_alert_state_for_today()

    assert svc._highest_positive_alert_bucket == {}
    assert svc._lowest_negative_alert_bucket == {}


@pytest.mark.asyncio
async def test_legacy_last_alert_bucket_is_split_by_sign(mocker):
    mocker.patch(
        "services.favorite_price_alert_service.StrategyStateIO.load",
        AsyncMock(return_value={
            "date": "20260821",
            "highest_positive_alert_bucket": "잘못된형식",
            "last_alert_bucket": {"005930": 2, "000660": -3, "": 1,
                                  "035420": 0, "005380": "숫자아님"},
            "upper_limit_alerted_codes": ["012330"],
        }),
    )
    svc, _, _ = _build(state_file="/tmp/alert-state.json", today_provider=lambda: "20260821")

    await svc._load_alert_state_for_today()

    assert svc._highest_positive_alert_bucket == {"005930": 2}
    assert svc._lowest_negative_alert_bucket == {"000660": -3}
    assert svc._upper_limit_alerted_codes == {"012330"}


# --- 부호 보정 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "rate, raw_rate, sign, change, expected",
    [
        (5.0, "5.0", "5", None, -5.0),          # KIS 하락 부호는 절대값을 뒤집는다
        (-5.0, "-5.0", "2", None, 5.0),         # KIS 상승 부호는 절대값을 되돌린다
        (5.0, "+5.0", None, None, 5.0),         # 원문에 부호가 있으면 그대로 신뢰한다
        (5.0, "5.0", None, "-1200", -5.0),      # 전일대비가 음수면 하락으로 본다
        (-5.0, None, None, "1200", 5.0),        # 원문 부호가 없고 전일대비가 양수면 상승
        (5.0, "5.0", None, "0", 0.0),           # 보합
        (5.0, "5.0", None, None, 5.0),          # 단서가 없으면 원값 유지
    ],
)
def test_signed_rate_resolution(rate, raw_rate, sign, change, expected):
    svc, _, _ = _build()

    assert svc._resolve_signed_rate("005930", rate, raw_rate, sign, change) == expected


def test_positive_rate_is_flipped_when_the_code_was_already_falling_today():
    svc, _, _ = _build()
    svc._lowest_negative_alert_bucket["005930"] = -2

    assert svc._resolve_signed_rate("005930", 5.0, "5.0", None, None) == -5.0


# --- 소소한 포매팅/조회 헬퍼 --------------------------------------------------

def test_stock_name_falls_back_to_the_code_on_lookup_failure():
    stock_codes = MagicMock()
    stock_codes.get_name_by_code.side_effect = RuntimeError("csv 없음")
    svc, _, _ = _build(stock_codes=stock_codes)

    assert svc._stock_name("005930") == "005930"


def test_stock_name_falls_back_to_the_code_when_the_repository_returns_blank():
    stock_codes = MagicMock()
    stock_codes.get_name_by_code.return_value = ""
    svc, _, _ = _build(stock_codes=stock_codes)

    assert svc._stock_name("005930") == "005930"


@pytest.mark.parametrize(
    "market, raw, expected",
    [
        (MARKET_OVERSEAS_US, " aapl ", "AAPL"),
        (MARKET_DOMESTIC, "5930", "005930"),
        (MARKET_DOMESTIC, "KR7005930003", "KR7005930003"),  # 6자리 숫자가 아니면 그대로
        (MARKET_DOMESTIC, None, ""),
    ],
)
def test_code_normalization(market, raw, expected):
    svc, _, _ = _build(market=market)

    assert svc._normalize_code(raw) == expected


@pytest.mark.parametrize("raw, expected", [(None, None), ("", None), (" - ", None),
                                           ("1,050", 1050.0), (3, 3.0)])
def test_float_coercion(raw, expected):
    svc, _, _ = _build()

    assert svc._to_float(raw) == expected


@pytest.mark.parametrize(
    "market, raw, expected",
    [
        (MARKET_DOMESTIC, "없음", "-"),
        (MARKET_DOMESTIC, "75000", "75,000원"),
        (MARKET_DOMESTIC, "75000.5", "75,000.50원"),
        (MARKET_OVERSEAS_US, "231.4", "$231.40"),
    ],
)
def test_price_formatting(market, raw, expected):
    svc, _, _ = _build(market=market)

    assert svc._format_price(raw) == expected
