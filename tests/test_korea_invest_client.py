import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient


def test_korea_invest_api_client_initialization():
    # 1. mock env 구성
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "test-access-token",
        "api_key": "test-app-key",
        "api_secret_key": "test-app-secret",
        "base_url": "https://mock-base-url",
        "is_paper_trading": True
    }
    mock_env.my_agent = "mock-user-agent"
    mock_token_manager = MagicMock()

    # 2. 각 도메인 API 클래스들도 patch
    from brokers.korea_investment.korea_invest_client import (
        KoreaInvestApiQuotations,
        KoreaInvestApiAccount,
        KoreaInvestApiTrading,
        KoreaInvestWebSocketAPI,
    )

    quotations_path = f"{KoreaInvestApiQuotations.__module__}.KoreaInvestApiQuotations"
    account_path = f"{KoreaInvestApiAccount.__module__}.KoreaInvestApiAccount"
    trading_path = f"{KoreaInvestApiTrading.__module__}.KoreaInvestApiTrading"
    ws_path = f"{KoreaInvestWebSocketAPI.__module__}.KoreaInvestWebSocketAPI"

    with patch(quotations_path) as mock_quotations, \
            patch(account_path) as mock_account, \
            patch(trading_path) as mock_trading, \
            patch(ws_path) as mock_ws:

        client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)

        # 3. 인스턴스 존재 확인
        mock_quotations.assert_called_once()
        mock_account.assert_called_once()
        mock_trading.assert_called_once()
        mock_ws.assert_called_once()

        assert client._config["access_token"] == "test-access-token"
        assert str(client).startswith("KoreaInvestApiClient(")


@pytest.mark.asyncio
async def test_quotations_get_price_summary_success():
    # env와 config mock
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "dummy-access-token",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P",
        "is_paper_trading": False
    }

    # 1. TokenManager에 대한 모의(mock) 객체를 생성합니다.
    mock_token_manager = MagicMock()

    # 2. KoreaInvestApiClient 생성자를 호출할 때 mock_token_manager를 전달합니다.
    client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)

    # quotations.call_api 비동기 메서드 모킹 (실제 네트워크 호출 차단)
    client._quotations.call_api = AsyncMock(return_value={
        "output": {
            "stck_oprc": "10000",
            "stck_prpr": "10500"
        }
    })

    # get_price_summary 호출
    result = await client._quotations.get_price_summary("005930")

    assert result["symbol"] == "005930"
    assert result["open"] == 10000
    assert result["current"] == 10500
    assert abs(result["change_rate"] - 5.0) < 0.01


@pytest.mark.asyncio
async def test_client_str_and_missing_access_token():
    # 정상 케이스
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "dummy-access-token",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",  # 추가 필수
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P",
        "is_paper_trading": False
    }
    # 1. TokenManager에 대한 모의(mock) 객체를 생성합니다.
    mock_token_manager = MagicMock()

    # 2. KoreaInvestApiClient 생성자를 호출할 때 mock_token_manager를 전달합니다.
    client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)
    expected_str = "KoreaInvestApiClient(base_url=https://mock-base, is_paper_trading=False)"
    assert str(client) == expected_str

    # access_token 누락 시 ValueError 발생
    mock_env2 = MagicMock()
    mock_env2.get_full_config.return_value = {
        "access_token": None,
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",  # 추가 필수
        "tr_ids": {
            "quotations": {
                "inquire_price": "dummy-tr-id"
            }
        },
        "custtype": "P",
        "is_paper_trading": False
    }
    import pytest
    with pytest.raises(ValueError, match="접근 토큰이 없습니다"):
        KoreaInvestApiClient(env=mock_env2, token_manager=mock_token_manager)


@pytest.fixture
def korea_invest_client_instance(mocker):
    """
    KoreaInvestApiClient 인스턴스를 생성하고, 내부의 _quotations, _account, _trading, _websocketAPI를 모킹합니다.
    """
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": "test-access-token",
        "api_key": "test-app-key",
        "api_secret_key": "test-app-secret",
        "base_url": "https://mock-base-url",
        "websocket_url": "wss://mock-websocket-url",  # websocketAPI 초기화에 필요
        "tr_ids": {  # TR_ID 관련 테스트를 위해 추가
            "quotations": {
                "inquire_price": "TR_INQUIRE_PRICE",
                "search_info": "TR_SEARCH_INFO",
                "top_market_cap": "TR_TOP_MARKET_CAP",
                "daily_itemchartprice_day": "TR_DAILY_CHART_D",
                "daily_itemchartprice_minute": "TR_DAILY_CHART_M",
            },
            "account": {
                "inquire_balance_paper": "TR_BALANCE_PAPER",
                "inquire_balance_real": "TR_BALANCE_REAL",
            },
            "trading": {
                "order_cash_buy_paper": "TR_BUY_PAPER",
                "order_cash_buy_real": "TR_BUY_REAL",
                "order_cash_sell_paper": "TR_SELL_PAPER",
                "order_cash_sell_real": "TR_SELL_REAL",
            },
            "websocket": {
                "realtime_price": "H0STCNT0",
                "realtime_quote": "H0STASP0",
            }
        },
        "custtype": "P",
        "is_paper_trading": True  # 모의투자 환경으로 설정
    }
    mock_env.my_agent = "mock-user-agent"
    mock_token_manager = MagicMock()
    mock_logger = MagicMock()  # 로거 모킹

    # 내부 도메인 API 클래스들을 모킹
    mock_quotations_class = mocker.patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiQuotations")
    mock_account_class = mocker.patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiAccount")
    mock_trading_class = mocker.patch("brokers.korea_investment.korea_invest_client.KoreaInvestApiTrading")
    mock_websocket_api_class = mocker.patch("brokers.korea_investment.korea_invest_client.KoreaInvestWebSocketAPI")

    # 각 모의 객체의 인스턴스 (AsyncMock으로 비동기 메서드 지원)
    mock_quotations_instance = AsyncMock()
    mock_account_instance = AsyncMock()
    mock_trading_instance = AsyncMock()
    mock_websocket_api_instance = AsyncMock()

    # 각 클래스가 인스턴스화될 때 이 모의 객체들을 반환하도록 설정
    mock_quotations_class.return_value = mock_quotations_instance
    mock_account_class.return_value = mock_account_instance
    mock_trading_class.return_value = mock_trading_instance
    mock_websocket_api_class.return_value = mock_websocket_api_instance

    # KoreaInvestApiClient 인스턴스 생성
    client = KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager, logger=mock_logger)

    # 픽스처에서 모의 객체들을 반환하여 테스트에서 접근할 수 있도록 함
    return client, mock_quotations_instance, mock_account_instance, mock_trading_instance, mock_websocket_api_instance, mock_logger, mock_env


def test_korea_invest_api_client_initialization(korea_invest_client_instance):
    """KoreaInvestApiClient 초기화가 정상적으로 이루어지는지 테스트합니다."""
    client, mock_quotations, mock_account, mock_trading, mock_websocket_api, mock_logger, mock_env = korea_invest_client_instance

    # 인스턴스 존재 확인 (픽스처에서 이미 모킹되었으므로, 여기서 assert_called_once_with를 직접 호출할 필요는 없습니다)
    # 대신, client._quotations 등이 올바른 mock 인스턴스를 참조하는지 확인합니다.
    assert client._quotations is mock_quotations
    assert client._account is mock_account
    assert client._trading is mock_trading
    assert client._websocketAPI is mock_websocket_api  # public 속성도 확인

    # _config 내용 확인
    assert client._config["access_token"] == "test-access-token"
    assert client._config["base_url"] == "https://mock-base-url"
    assert client._config["is_paper_trading"] is True  # is_paper_trading 확인

    # __str__ 메서드 테스트
    expected_str = "KoreaInvestApiClient(base_url=https://mock-base-url, is_paper_trading=True)"
    assert str(client) == expected_str


def test_client_initialization_missing_access_token():
    """access_token 누락 시 ValueError가 발생하는지 테스트합니다."""
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "access_token": None,  # access_token 누락
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "websocket_url": "wss://mock-websocket-url",
        "tr_ids": {},  # 최소한의 tr_ids
        "custtype": "P",
        "is_paper_trading": False
    }
    mock_token_manager = MagicMock()

    with pytest.raises(ValueError, match="접근 토큰이 없습니다"):
        KoreaInvestApiClient(env=mock_env, token_manager=mock_token_manager)


@pytest.mark.asyncio
async def test_quotations_get_price_summary_success(korea_invest_client_instance):
    """get_price_summary 메서드 호출 성공 케이스를 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance

    # get_price_summary의 반환 값 설정
    mock_quotations.get_price_summary.return_value = {
        "symbol": "005930",
        "open": 10000,
        "current": 10500,
        "change_rate": 5.0
    }

    # get_price_summary 호출
    result = await client.get_price_summary("005930")

    # 검증
    mock_quotations.get_price_summary.assert_awaited_once_with("005930")
    assert result["symbol"] == "005930"
    assert result["open"] == 10000
    assert result["current"] == 10500
    assert abs(result["change_rate"] - 5.0) < 0.01


# --- Account API delegation 테스트 (라인 57, 60 커버) ---
@pytest.mark.asyncio
async def test_get_account_balance_delegation(korea_invest_client_instance):
    """get_account_balance 메서드가 _account.get_account_balance를 올바르게 호출하는지 테스트합니다."""
    client, _, mock_account, _, _, _, _ = korea_invest_client_instance
    mock_account.get_account_balance.return_value = {"output": "account_balance_paper_data"}

    result = await client.get_account_balance()

    mock_account.get_account_balance.assert_awaited_once()
    assert result == {"output": "account_balance_paper_data"}


@pytest.mark.asyncio
async def test_get_real_account_balance_delegation(korea_invest_client_instance):
    """get_real_account_balance 메서드가 _account.get_real_account_balance를 올바르게 호출하는지 테스트합니다."""
    client, _, mock_account, _, _, _, _ = korea_invest_client_instance
    mock_account.get_real_account_balance.return_value = {"output": "account_balance_real_data"}

    result = await client.get_real_account_balance()

    mock_account.get_real_account_balance.assert_awaited_once()
    assert result == {"output": "account_balance_real_data"}


# --- Trading API delegation 테스트 (라인 64, 67, 71 커버) ---
@pytest.mark.asyncio
async def test_buy_stock_delegation(korea_invest_client_instance):
    """buy_stock 메서드가 _trading.place_stock_order를 'buy' 타입으로 호출하는지 테스트합니다."""
    client, _, _, mock_trading, _, _, _ = korea_invest_client_instance
    mock_trading.place_stock_order.return_value = {"rt_cd": "0", "msg1": "매수 주문 성공"}

    result = await client.buy_stock("005930", 70000, 10, "지정가", "01")

    mock_trading.place_stock_order.assert_awaited_once_with("005930", 70000, 10, "buy", "01")
    assert result == {"rt_cd": "0", "msg1": "매수 주문 성공"}


@pytest.mark.asyncio
async def test_sell_stock_delegation(korea_invest_client_instance):
    """sell_stock 메서드가 _trading.place_stock_order를 'sell' 타입으로 호출하는지 테스트합니다."""
    client, _, _, mock_trading, _, _, _ = korea_invest_client_instance
    mock_trading.place_stock_order.return_value = {"rt_cd": "0", "msg1": "매도 주문 성공"}

    result = await client.sell_stock("005930", 69000, 5, "지정가", "01")

    mock_trading.place_stock_order.assert_awaited_once_with("005930", 69000, 5, "sell", "01")
    assert result == {"rt_cd": "0", "msg1": "매도 주문 성공"}


@pytest.mark.asyncio
async def test_place_stock_order_delegation(korea_invest_client_instance):
    """place_stock_order 메서드가 _trading.place_stock_order를 올바른 인자로 호출하는지 테스트합니다."""
    client, _, _, mock_trading, _, _, _ = korea_invest_client_instance
    mock_trading.place_stock_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}

    result = await client.place_stock_order("005930", 70000, 10, "buy", "00")  # 시장가 매수

    mock_trading.place_stock_order.assert_awaited_once_with("005930", 70000, 10, "buy", "00")
    assert result == {"rt_cd": "0", "msg1": "주문 성공"}


# --- Quotations API delegation 테스트 (라인 77, 81, 89, 93, 98, 104, 109 커버) ---
@pytest.mark.asyncio
async def test_get_stock_info_by_code_delegation(korea_invest_client_instance):
    """get_stock_info_by_code 메서드가 _quotations.get_stock_info_by_code를 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance
    mock_quotations.get_stock_info_by_code.return_value = {"hts_kor_isnm": "삼성전자"}

    result = await client.get_stock_info_by_code("005930")

    mock_quotations.get_stock_info_by_code.assert_awaited_once_with("005930")
    assert result == {"hts_kor_isnm": "삼성전자"}


@pytest.mark.asyncio
async def test_get_current_price_delegation(korea_invest_client_instance):
    """get_current_price 메서드가 _quotations.get_current_price를 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance
    mock_quotations.get_current_price.return_value = {"output": {"stck_prpr": "75000"}}

    result = await client.get_current_price("005930")

    mock_quotations.get_current_price.assert_awaited_once_with("005930")
    assert result == {"output": {"stck_prpr": "75000"}}


@pytest.mark.asyncio
async def test_get_market_cap_delegation(korea_invest_client_instance):
    """get_market_cap 메서드가 _quotations.get_market_cap을 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance
    mock_quotations.get_market_cap.return_value = 500000000000000  # 500조

    result = await client.get_market_cap("005930")

    mock_quotations.get_market_cap.assert_awaited_once_with("005930")
    assert result == 500000000000000


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_code_delegation(korea_invest_client_instance):
    """get_top_market_cap_stocks_code 메서드가 _quotations.get_top_market_cap_stocks_code를 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance
    mock_quotations.get_top_market_cap_stocks_code.return_value = {"rt_cd": "0", "output": [{"iscd": "005930"}]}

    result = await client.get_top_market_cap_stocks_code("0000", 1)

    mock_quotations.get_top_market_cap_stocks_code.assert_awaited_once_with("0000", 1)
    assert result == {"rt_cd": "0", "output": [{"iscd": "005930"}]}


def test_get_previous_day_info_delegation(korea_invest_client_instance):
    """get_previous_day_info 메서드가 _quotations.get_previous_day_info를 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance

    mock_quotations.get_previous_day_info = MagicMock(return_value={"prev_close": 65000, "prev_volume": 100000})

    result = client.get_previous_day_info("005930")  # 동기 메서드

    mock_quotations.get_previous_day_info.assert_called_once_with("005930")
    assert result == {"prev_close": 65000, "prev_volume": 100000}


@pytest.mark.asyncio
async def test_get_filtered_stocks_by_momentum_delegation(korea_invest_client_instance):
    """get_filtered_stocks_by_momentum 메서드가 _quotations.get_filtered_stocks_by_momentum을 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance
    mock_quotations.get_filtered_stocks_by_momentum.return_value = [{"symbol": "005930", "change_rate": 15.0}]

    result = await client.get_filtered_stocks_by_momentum(count=5, min_change_rate=10.0, min_volume_ratio=3.0)

    mock_quotations.get_filtered_stocks_by_momentum.assert_awaited_once_with(5, 10.0, 3.0)
    assert result == [{"symbol": "005930", "change_rate": 15.0}]


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_delegation(korea_invest_client_instance):
    """inquire_daily_itemchartprice 메서드가 _quotations.inquire_daily_itemchartprice를 호출하는지 테스트합니다."""
    client, mock_quotations, _, _, _, _, _ = korea_invest_client_instance
    mock_quotations.inquire_daily_itemchartprice.return_value = [{"date": "20230101", "price": "70000"}]

    result = await client.inquire_daily_itemchartprice("005930", "20230101", "D")

    mock_quotations.inquire_daily_itemchartprice.assert_awaited_once_with("005930", "20230101", fid_period_div_code="D")
    assert result == [{"date": "20230101", "price": "70000"}]


# --- WebSocket API delegation 테스트 (라인 114, 118, 122, 126, 130, 134 커버) ---
@pytest.mark.asyncio
async def test_connect_websocket_delegation(korea_invest_client_instance):
    """connect_websocket 메서드가 _websocketAPI.connect를 호출하는지 테스트합니다."""
    client, _, _, _, mock_websocket_api, _, _ = korea_invest_client_instance
    mock_websocket_api.connect.return_value = True

    mock_callback = MagicMock()
    result = await client.connect_websocket(mock_callback)

    mock_websocket_api.connect.assert_awaited_once_with(mock_callback)
    assert result is True


@pytest.mark.asyncio
async def test_disconnect_websocket_delegation(korea_invest_client_instance):
    """disconnect_websocket 메서드가 _websocketAPI.disconnect를 호출하는지 테스트합니다."""
    client, _, _, _, mock_websocket_api, _, _ = korea_invest_client_instance
    mock_websocket_api.disconnect.return_value = True

    result = await client.disconnect_websocket()

    mock_websocket_api.disconnect.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
async def test_subscribe_realtime_price_delegation(korea_invest_client_instance):
    """subscribe_realtime_price 메서드가 _websocketAPI.subscribe_realtime_price를 호출하는지 테스트합니다."""
    client, _, _, _, mock_websocket_api, _, _ = korea_invest_client_instance
    mock_websocket_api.subscribe_realtime_price.return_value = True

    result = await client.subscribe_realtime_price("005930")

    mock_websocket_api.subscribe_realtime_price.assert_awaited_once_with("005930")
    assert result is True


@pytest.mark.asyncio
async def test_unsubscribe_realtime_price_delegation(korea_invest_client_instance):
    """unsubscribe_realtime_price 메서드가 _websocketAPI.unsubscribe_realtime_price를 호출하는지 테스트합니다."""
    client, _, _, _, mock_websocket_api, _, _ = korea_invest_client_instance
    mock_websocket_api.unsubscribe_realtime_price.return_value = True

    result = await client.unsubscribe_realtime_price("005930")

    mock_websocket_api.unsubscribe_realtime_price.assert_awaited_once_with("005930")
    assert result is True


@pytest.mark.asyncio
async def test_subscribe_realtime_quote_delegation(korea_invest_client_instance):
    """subscribe_realtime_quote 메서드가 _websocketAPI.subscribe_realtime_quote를 호출하는지 테스트합니다."""
    client, _, _, _, mock_websocket_api, _, _ = korea_invest_client_instance
    mock_websocket_api.subscribe_realtime_quote.return_value = True

    result = await client.subscribe_realtime_quote("005930")

    mock_websocket_api.subscribe_realtime_quote.assert_awaited_once_with("005930")
    assert result is True


@pytest.mark.asyncio
async def test_unsubscribe_realtime_quote_delegation(korea_invest_client_instance):
    """unsubscribe_realtime_quote 메서드가 _websocketAPI.unsubscribe_realtime_quote를 호출하는지 테스트합니다."""
    client, _, _, _, mock_websocket_api, _, _ = korea_invest_client_instance
    mock_websocket_api.unsubscribe_realtime_quote.return_value = True

    result = await client.unsubscribe_realtime_quote("005930")

    mock_websocket_api.unsubscribe_realtime_quote.assert_awaited_once_with("005930")
    assert result is True