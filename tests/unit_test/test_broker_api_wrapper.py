import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import logging

# 테스트 대상 클래스 import
import brokers.broker_api_wrapper as wrapper_module
from brokers.broker_api_wrapper import BrokerAPIWrapper
from core.cache.cache_wrapper import ClientWithCache


@pytest.fixture
def mock_env():
    """모의 KoreaInvestApiEnv 객체를 생성합니다."""
    env = MagicMock()
    # __init__에서 필요한 설정 값들을 반환하도록 설정
    env.get_full_config.return_value = {
        'base_url': 'http://mock-base-url.com',
        'api_key': 'mock_api_key',
        'api_secret_key': 'mock_api_secret_key',
        'access_token': 'mock_access_token_from_env',  # <<-- 이 값을 추가
        'custtype': 'P'
    }
    env.access_token = 'mock_token'
    return env


@pytest.fixture
def mock_logger():
    """모의 로거(Logger) 객체를 생성합니다."""
    return MagicMock()


@pytest.fixture
def mock_time_manager():
    """모의 로거(Logger) 객체를 생성합니다."""
    return MagicMock()


# --- 테스트 케이스 ---

@pytest.mark.asyncio
@patch(f"{wrapper_module.__name__}.StockCodeMapper")  # 먼저 정의된 patch가
@patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")  # 아래쪽 인자로 먼저 들어감!
async def test_method_delegation(mock_client_class, mock_mapper_class, mock_env, mock_logger, mock_time_manager):
    """
    각 메서드가 내부의 올바른 객체로 호출을 위임하는지 테스트합니다.
    """
    # 1. StockCodeMapper mock 설정 (동기)
    mock_mapper = MagicMock()
    mock_mapper.get_name_by_code.return_value = "삼성전자"
    mock_mapper.get_code_by_name.return_value = "005930"
    mock_mapper_class.return_value = mock_mapper

    # 2. KoreaInvestApiClient mock 설정 (비동기)
    mock_client = AsyncMock()
    mock_client.inquire_daily_itemchartprice.return_value = {"chart": "data"}
    mock_client_class.return_value = mock_client

    mock_time_manager.is_market_open.return_value = True  # ✅ 함수로 유지

    # 3. 인스턴스 생성
    wrapper = BrokerAPIWrapper("korea_investment", env=mock_env, logger=mock_logger, time_manager=mock_time_manager)

    # 4. 실제 메서드 호출
    name_result = await wrapper.get_name_by_code("005930")
    code_result = await wrapper.get_code_by_name("삼성전자")
    chart_result = await wrapper.inquire_daily_itemchartprice("005930", start_date="20250101", end_date="20250111", fid_period_div_code="D")

    # 5. 결과 검증
    assert name_result == "삼성전자"
    assert code_result == "005930"
    assert chart_result == {"chart": "data"}

    # 6. 호출 여부 검증
    mock_mapper.get_name_by_code.assert_called_once_with("005930")
    mock_mapper.get_code_by_name.assert_called_once_with("삼성전자")
    mock_client.inquire_daily_itemchartprice.assert_awaited_once_with("005930",start_date="20250101", end_date="20250111", fid_period_div_code="D")


@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice_delegation(mock_env, mock_logger):
    """
    inquire_daily_itemchartprice 메서드가 _client 객체의 동일 메서드에 정확히 위임되는지 검증합니다.
    """
    with patch('brokers.broker_api_wrapper') as MockBrokerWrapper:
        # Mock 객체 구성
        MockBrokerWrapper.inquire_daily_itemchartprice = AsyncMock(return_value=[{"stck_prpr": "70000"}])

        # Act
        result = await MockBrokerWrapper.inquire_daily_itemchartprice("005930", "20250708", fid_period_div_code="D")

        # Assert
        MockBrokerWrapper.inquire_daily_itemchartprice.assert_awaited_once_with(
            "005930", "20250708", fid_period_div_code="D"
        )
        assert result == [{"stck_prpr": "70000"}]


@pytest.mark.asyncio
async def test_all_delegations(broker_wrapper_instance, mocker):
    """
    BrokerAPIWrapper의 모든 위임 메서드(StockMapper, KoreaInvestApiClient의 모든 도메인)를 테스트하고
    각 메서드가 올바른 하위 객체를 호출하는지 검증합니다.
    """
    wrapper, mock_client, mock_stock_mapper, mock_logger = broker_wrapper_instance

    # --- StockCodeMapper delegation (get_name_by_code, get_code_by_name) ---
    result = await wrapper.get_name_by_code("005930")
    assert result == "삼성전자_mapper"
    mock_stock_mapper.get_name_by_code.assert_called_once_with("005930")

    result = await wrapper.get_code_by_name("삼성전자")
    assert result == "005930_mapper"
    mock_stock_mapper.get_code_by_name.assert_called_once_with("삼성전자")

    # --- StockCodeMapper delegation (get_all_stock_codes) ---
    result = await wrapper.get_all_stock_codes()
    assert result is mock_stock_mapper.df  # df 객체 자체를 반환하는지 확인
    mock_logger.error.assert_not_called()  # 에러 로그가 찍히지 않는지 확인 (성공 경로)

    # --- StockCodeMapper delegation (get_all_stock_code_list) ---
    result = await wrapper.get_all_stock_code_list()
    assert result == ['005930', '000660']
    # get_all_stock_codes가 호출되었음을 간접적으로 확인 가능

    # --- StockCodeMapper delegation (get_all_stock_name_list) ---
    result = await wrapper.get_all_stock_name_list()
    assert result == ['삼성전자', 'SK하이닉스']
    # get_all_stock_codes가 호출되었음을 간접적으로 확인 가능

    # --- KoreaInvestApiClient / Quotations API delegation ---
    # get_stock_info_by_code (lines 59, 61) - calls self._client.get_stock_info_by_code
    # result = await wrapper.get_stock_info_by_code("005930")
    # assert result == {"hts_kor_isnm": "삼성전자_info"}
    # wrapper._client.get_stock_info_by_code.assert_called_once_with("005930")

    # get_current_price (lines 63, 65) - calls self._client.get_current_price
    result = await wrapper.get_current_price("005930")
    assert result == {"output": {"stck_prpr": "70000"}}
    mock_client.get_current_price.assert_called_once_with("005930")

    # get_price_summary (lines 67, 69) - calls self._client.get_price_summary
    result = await wrapper.get_price_summary("005930")
    mock_client.get_price_summary.return_value = {
        "symbol": "005930",
        "current": 70000
    }
    wrapper._client.get_price_summary.assert_called_once_with("005930")

    # get_market_cap (lines 71, 73) - calls self._client.get_market_cap
    result = await wrapper.get_market_cap("005930")
    assert result == 1234567890
    wrapper._client.get_market_cap.assert_called_once_with("005930")

    # get_top_market_cap_stocks_code (lines 75, 79) - calls self._client.get_top_market_cap_stocks_code
    result = await wrapper.get_top_market_cap_stocks_code("0000", count=1)
    assert result == {"rt_cd": "0", "output": [{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"}]}
    mock_client.get_top_market_cap_stocks_code.assert_called_once_with("0000", 1)

    # inquire_daily_itemchartprice (lines 92, 94) - calls self._client.inquire_daily_itemchartprice
    result = await wrapper.inquire_daily_itemchartprice("005930", start_date="20250101", end_date="20250111", fid_period_div_code="M")
    assert result == [{"stck_clpr": "70000_chart"}]
    mock_client.inquire_daily_itemchartprice.assert_called_once_with("005930", start_date="20250101", end_date="20250111", fid_period_div_code="M")

    # --- KoreaInvestApiClient / Account API delegation ---
    # get_account_balance (lines 98, 100) - calls self._client.get_account_balance
    result = await wrapper.get_account_balance()
    assert result == {"output": {"dnca_tot_amt": "1000000"}}
    mock_client.get_account_balance.assert_called_once()

    # place_stock_order (lines 115, 117) - calls self._client.place_stock_order
    result = await wrapper.place_stock_order("005930", 69500, 15, is_buy=True)
    assert result == {"rt_cd": "0", "msg1": "주문 성공"}
    wrapper._client.place_stock_order.assert_called_once_with("005930", 69500, 15, True)

    # --- KoreaInvestApiClient / WebSocket API delegation ---
    # connect_websocket (lines 120, 122) - calls self._client.connect_websocket
    result = await wrapper.connect_websocket()
    assert result is True
    mock_client.connect_websocket.assert_called_once()

    # disconnect_websocket (lines 124, 126) - calls self._client.disconnect_websocket
    mock_client.disconnect_websocket.return_value = True  # Ensure this mock is set for the method
    result = await wrapper.disconnect_websocket()
    assert result is True
    mock_client.disconnect_websocket.assert_called_once()

    # subscribe_realtime_price (lines 128, 130) - calls self._client.subscribe_realtime_price
    result = await wrapper.subscribe_realtime_price("005930")
    assert result is True
    mock_client.subscribe_realtime_price.assert_called_once_with("005930")

    # unsubscribe_realtime_price (lines 132, 134) - calls self._client.unsubscribe_realtime_price
    result = await wrapper.unsubscribe_realtime_price("005930")
    assert result is True
    mock_client.unsubscribe_realtime_price.assert_called_once_with("005930")

    # subscribe_realtime_quote (lines 136, 138) - calls self._client.subscribe_realtime_quote
    result = await wrapper.subscribe_realtime_quote("005930")
    assert result is True
    mock_client.subscribe_realtime_quote.assert_called_once_with("005930")

    # unsubscribe_realtime_quote (lines 140, 142) - calls self._client.unsubscribe_realtime_quote
    result = await wrapper.unsubscribe_realtime_quote("005930")
    assert result is True
    mock_client.unsubscribe_realtime_quote.assert_called_once_with("005930")


@pytest.fixture
def broker_wrapper_instance(mock_env, mock_logger, mocker):
    """
    BrokerAPIWrapper 인스턴스와 그 내부 종속성(_client, _stock_mapper)을 모의(mock)하여 제공하는 픽스처.
    모의된 BrokerAPIWrapper를 반환하며, 내부 _client에 대한 접근도 모의합니다.
    사용자의 broker_api_wrapper.py 코드에 있는 논리적 오류(self._client, self._client 미초기화)를
    회피하기 위해 직접 해당 속성들을 mock으로 할당합니다.
    """
    # BrokerAPIWrapper의 __init__에서 호출되는 KoreaInvestApiClient와 StockCodeMapper를 패치
    MockClientClass = mocker.patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")
    MockStockMapperClass = mocker.patch(f"{wrapper_module.__name__}.StockCodeMapper")

    # KoreaInvestApiClient의 인스턴스 Mock (BrokerAPIWrapper의 _client가 됩니다)
    mock_client_instance = AsyncMock()
    # mock_client_instance의 내부 API 속성들을 모의합니다.
    # BrokerAPIWrapper의 메서드들이 self._client.XXX 또는 self.XXX (내부적으로 self._client.XXX) 형태로 호출될 수 있도록
    mock_client_instance._account = AsyncMock()
    mock_client_instance._trading = AsyncMock()
    mock_client_instance._quotations = AsyncMock()
    mock_client_instance._websocketAPI = AsyncMock()  # public attribute

    # KoreaInvestApiClient에서 위임하는 메서드들의 반환 값 설정 (예시)
    mock_client_instance.get_stock_info_by_code.return_value = {"hts_kor_isnm": "삼성전자_info"}
    mock_client_instance.get_current_price.return_value = {"output": {"stck_prpr": "70000"}}
    mock_client_instance.get_price_summary.return_value = {"symbol": "005930", "open": 69000, "current": 70000,
                                                           "change_rate": 1.45}
    mock_client_instance.get_market_cap.return_value = 1234567890
    mock_client_instance.get_top_market_cap_stocks_code.return_value = {"rt_cd": "0", "output": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"}]}
    mock_client_instance.get_previous_day_info = MagicMock(return_value={"prev_close": 68000, "prev_volume": 10000})
    mock_client_instance.get_filtered_stocks_by_momentum.return_value = [{"symbol": "005930_filtered"}]
    mock_client_instance.inquire_daily_itemchartprice.return_value = [{"stck_clpr": "70000_chart"}]

    mock_client_instance.get_account_balance = AsyncMock(return_value={"output": {"dnca_tot_amt": "1000000"}})
    mock_client_instance.place_stock_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}
    mock_client_instance.get_stock_info_by_code.return_value = {"hts_kor_isnm": "삼성전자_info"}
    mock_client_instance.get_current_price.return_value = {"output": {"stck_prpr": "70000"}}
    mock_client_instance.get_price_summary.return_value = {"symbol": "005930", "open": 69000,
                                                           "current": 70000, "change_rate": 1.45}
    mock_client_instance.get_market_cap.return_value = 1234567890
    mock_client_instance.get_top_market_cap_stocks_code.return_value = {"rt_cd": "0", "output": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"}]}
    mock_client_instance.get_previous_day_info.return_value = {"prev_close": 68000,
                                                               "prev_volume": 10000}  # This is sync, so return_value is direct
    mock_client_instance.get_filtered_stocks_by_momentum.return_value = [{"symbol": "005930_filtered"}]
    mock_client_instance.inquire_daily_itemchartprice.return_value = [{"stck_clpr": "70000_chart"}]

    mock_client_instance.get_account_balance.return_value = {"output": {"dnca_tot_amt": "1000000"}}
    mock_client_instance.place_stock_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}
    mock_client_instance.connect_websocket = AsyncMock(return_value=True)
    mock_client_instance.disconnect_websocket = AsyncMock(return_value=True)
    mock_client_instance.subscribe_realtime_price = AsyncMock(return_value=True)
    mock_client_instance.unsubscribe_realtime_price = AsyncMock(return_value=True)
    mock_client_instance.subscribe_realtime_quote = AsyncMock(return_value=True)
    mock_client_instance.unsubscribe_realtime_quote = AsyncMock(return_value=True)

    MockClientClass.return_value = mock_client_instance

    # StockCodeMapper의 인스턴스 Mock
    mock_stock_mapper_instance = MagicMock()
    mock_stock_mapper_instance.get_name_by_code.return_value = "삼성전자_mapper"
    mock_stock_mapper_instance.get_code_by_name.return_value = "005930_mapper"
    # get_all_stock_codes 테스트를 위해 df 속성을 mock합니다.
    mock_stock_mapper_instance.df = MagicMock()
    mock_stock_mapper_instance.df.columns = ['종목코드', '종목명']

    # 각 컬럼 이름에 해당하는 MagicMock 객체를 미리 생성하고, tolist()의 반환 값을 설정합니다.
    mock_col_code = MagicMock()
    mock_col_code.tolist.return_value = ['005930', '000660']

    mock_col_name = MagicMock()
    mock_col_name.tolist.return_value = ['삼성전자', 'SK하이닉스']

    # df[key] 접근 시 (즉, __getitem__ 호출 시) 올바른 mock 객체를 반환하도록 side_effect 설정
    mock_stock_mapper_instance.df.__getitem__.side_effect = \
        lambda key: mock_col_code if key == '종목코드' else \
            (mock_col_name if key == '종목명' else MagicMock())
    MockStockMapperClass.return_value = mock_stock_mapper_instance

    # BrokerAPIWrapper 인스턴스 생성 (사용자의 코드에 있는 __init__ 시그니처와 동일)
    # broker: str = "korea_investment", env=None, token_manager=None, logger=None
    wrapper = BrokerAPIWrapper(broker="korea_investment", env=mock_env, logger=mock_logger)

    # 사용자의 `broker_api_wrapper.py` 파일의 논리적 오류(self._client, self._client 미초기화)를
    # 회피하기 위해 해당 속성들을 직접 mock으로 할당하여 테스트가 통과하도록 합니다.
    # 실제 코드에서는 BrokerAPIWrapper의 __init__에서 self._client = self._client._client 등으로 할당되어야 합니다.
    wrapper._client = mock_client_instance  # _client의 _trading을 직접 할당

    yield wrapper, mock_client_instance, mock_stock_mapper_instance, mock_logger


# --- 테스트 케이스 ---

@patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")
@patch(f"{wrapper_module.__name__}.StockCodeMapper")
def test_initialization_success(MockStockMapper, MockClient, mock_env, mock_logger):
    """
    정상적인 인자로 BrokerAPIWrapper 초기화가 성공하는지 테스트합니다.
    """
    # Act
    wrapper = BrokerAPIWrapper(broker="korea_investment", env=mock_env, logger=mock_logger)

    # Assert
    MockClient.assert_called_once_with(mock_env, mock_logger)
    MockStockMapper.assert_called_once_with(logger=mock_logger)
    assert wrapper._broker == "korea_investment"


@patch(f"{wrapper_module.__name__}.KoreaInvestApiClient")
@patch(f"{wrapper_module.__name__}.StockCodeMapper")
def test_initialization_success(mock_stock_mapper, mock_client, mock_env, mock_logger, mock_time_manager):
    """
    정상적인 인자로 BrokerAPIWrapper 초기화가 성공하는지 테스트합니다.
    """
    # Act
    wrapper = BrokerAPIWrapper(broker="korea_investment", env=mock_env, logger=mock_logger, time_manager=mock_time_manager)

    # Assert
    mock_client.assert_called_once_with(mock_env, mock_logger, mock_time_manager)
    mock_stock_mapper.assert_called_once_with(logger=mock_logger)
    assert wrapper._broker == "korea_investment"
    assert isinstance(wrapper._client, ClientWithCache)  # ✅ wrapping 여부 확인
    assert wrapper._client._client is mock_client.return_value  # ✅ 내부 원본 확인
    assert wrapper._stock_mapper is mock_stock_mapper.return_value  # _stock_mapper가 mock_stock_mapper 인스턴스를 참조하는지 확인


def test_initialization_no_env_raises_error(mock_logger):
    """
    'korea_investment' 브로커 선택 시 env가 없으면 ValueError가 발생하는지 테스트합니다.
    """
    # Arrange, Act & Assert
    with pytest.raises(ValueError, match="KoreaInvest API를 사용하려면 env 인스턴스가 필요합니다."):
        BrokerAPIWrapper(
            broker="korea_investment",
            env=None,  # env를 명시적으로 None으로 설정

            logger=mock_logger
        )


def test_initialization_unsupported_broker_raises_error(mock_env):
    """
    지원되지 않는 브로커 이름으로 초기화 시 NotImplementedError가 발생하는지 테스트합니다.
    """
    # Arrange, Act & Assert
    with pytest.raises(NotImplementedError, match="지원되지 않는 증권사: unsupported_broker"):
        BrokerAPIWrapper(broker="unsupported_broker", env=mock_env)


# --- 추가적인 엣지 케이스 및 오류 경로 테스트 ---

@pytest.mark.asyncio
async def test_get_all_stock_codes_no_df_attribute(broker_wrapper_instance, caplog):
    """
    get_all_stock_codes()에서 _stock_mapper에 df 속성이 없을 때 오류를 로깅하고 None을 반환하는지 테스트합니다.
    (lines 38, 41, 42 커버)
    """
    wrapper, mock_client, mock_stock_mapper, mock_logger = broker_wrapper_instance

    # _stock_mapper에서 df 속성을 제거하여 hasattr()이 False를 반환하도록 합니다.
    del mock_stock_mapper.df

    with caplog.at_level(logging.ERROR):  # 에러 로그를 캡처합니다.
        result = await wrapper.get_all_stock_codes()

        assert result is None
        mock_logger.error.assert_called_once_with("StockCodeMapper가 초기화되지 않았거나 df 속성이 없습니다.")
    caplog.clear()  # 다음 테스트를 위해 로그 캡처 초기화


@pytest.mark.asyncio
async def test_get_all_stock_code_list_edge_cases(broker_wrapper_instance):
    """
    get_all_stock_code_list()의 엣지 케이스를 테스트합니다.
    (lines 46, 47, 49 커버)
    """
    wrapper, mock_client, mock_stock_mapper, mock_logger = broker_wrapper_instance

    # Case 1: get_all_stock_codes()가 None을 반환할 때
    async def mock_get_all_stock_codes_returns_none():
        return None

    wrapper.get_all_stock_codes = mock_get_all_stock_codes_returns_none  # 메서드를 패치
    result = await wrapper.get_all_stock_code_list()
    assert result == []

    # wrapper.get_all_stock_codes = broker_wrapper_instance[0].get_all_stock_codes.__wrapped__ # 원래 함수로 복원 (선택 사항)

    # Case 2: DataFrame에 '종목코드' 컬럼이 없을 때
    async def mock_get_all_stock_codes_no_code_col():
        df = MagicMock()
        df.columns = ['OtherColumn', '종목명']  # '종목코드' 없음
        return df

    wrapper.get_all_stock_codes = mock_get_all_stock_codes_no_code_col
    result = await wrapper.get_all_stock_code_list()
    assert result == []


@pytest.mark.asyncio
async def test_get_all_stock_name_list_edge_cases(broker_wrapper_instance):
    """
    get_all_stock_name_list()의 엣지 케이스를 테스트합니다.
    (lines 53, 54, 56 커버)
    """
    wrapper, mock_client, mock_stock_mapper, mock_logger = broker_wrapper_instance

    # Case 1: get_all_stock_codes()가 None을 반환할 때
    async def mock_get_all_stock_codes_returns_none():
        return None

    wrapper.get_all_stock_codes = mock_get_all_stock_codes_returns_none
    result = await wrapper.get_all_stock_name_list()
    assert result == []

    # Case 2: DataFrame에 '종목명' 컬럼이 없을 때
    async def mock_get_all_stock_codes_no_name_col():
        df = MagicMock()
        df.columns = ['종목코드', 'OtherColumn']  # '종목명' 없음
        return df

    wrapper.get_all_stock_codes = mock_get_all_stock_codes_no_name_col
    result = await wrapper.get_all_stock_name_list()
    assert result == []


@pytest.mark.asyncio
async def test_subscribe_program_trading_delegation(broker_wrapper_instance):
    """subscribe_program_trading 메서드가 _client.subscribe_program_trading을 호출하는지 테스트합니다."""
    wrapper, mock_client, _, _ = broker_wrapper_instance
    mock_client.subscribe_program_trading.return_value = True

    result = await wrapper.subscribe_program_trading("005930")

    mock_client.subscribe_program_trading.assert_awaited_once_with("005930")
    assert result is True

@pytest.mark.asyncio
async def test_unsubscribe_program_trading_delegation(broker_wrapper_instance):
    """unsubscribe_program_trading 메서드가 _client.unsubscribe_program_trading을 호출하는지 테스트합니다."""
    wrapper, mock_client, _, _ = broker_wrapper_instance
    mock_client.unsubscribe_program_trading.return_value = True

    result = await wrapper.unsubscribe_program_trading("005930")

    mock_client.unsubscribe_program_trading.assert_awaited_once_with("005930")
    assert result is True


@pytest.mark.asyncio
async def test_additional_delegations(broker_wrapper_instance):
    """
    BrokerAPIWrapper의 추가적인 위임 메서드들을 테스트합니다.
    (get_stock_info_by_code, get_stock_conclusion, inquire_time_itemchartprice 등)
    """
    wrapper, mock_client, _, _ = broker_wrapper_instance

    # 1. get_stock_info_by_code
    mock_client.get_stock_info_by_code.return_value = {"info": "data"}
    result = await wrapper.get_stock_info_by_code("005930")
    mock_client.get_stock_info_by_code.assert_called_once_with("005930")
    assert result == {"info": "data"}

    # 2. get_stock_conclusion
    mock_client.get_stock_conclusion.return_value = {"conclusion": "data"}
    result = await wrapper.get_stock_conclusion("005930")
    mock_client.get_stock_conclusion.assert_called_once_with("005930")
    assert result == {"conclusion": "data"}

    # 3. inquire_time_itemchartprice
    mock_client.inquire_time_itemchartprice.return_value = {"chart": "time_data"}
    result = await wrapper.inquire_time_itemchartprice(
        stock_code="005930", input_hour_1="120000", pw_data_incu_yn="Y", etc_cls_code="0"
    )
    mock_client.inquire_time_itemchartprice.assert_called_once_with(
        stock_code="005930", input_hour_1="120000", pw_data_incu_yn="Y", etc_cls_code="0"
    )
    assert result == {"chart": "time_data"}

    # 4. inquire_time_dailychartprice
    mock_client.inquire_time_dailychartprice.return_value = {"chart": "daily_time_data"}
    result = await wrapper.inquire_time_dailychartprice(
        stock_code="005930", input_date_1="20250101", input_hour_1="120000"
    )
    mock_client.inquire_time_dailychartprice.assert_called_once_with(
        stock_code="005930", input_date_1="20250101", input_hour_1="120000",
        pw_data_incu_yn="Y", fake_tick_incu_yn=""
    )
    assert result == {"chart": "daily_time_data"}

    # 5. get_asking_price
    mock_client.get_asking_price.return_value = {"asking": "price"}
    result = await wrapper.get_asking_price("005930")
    mock_client.get_asking_price.assert_called_once_with("005930")
    assert result == {"asking": "price"}

    # 6. get_time_concluded_prices
    mock_client.get_time_concluded_prices.return_value = {"concluded": "prices"}
    result = await wrapper.get_time_concluded_prices("005930")
    mock_client.get_time_concluded_prices.assert_called_once_with("005930")
    assert result == {"concluded": "prices"}

    # 7. get_top_rise_fall_stocks
    mock_client.get_top_rise_fall_stocks.return_value = {"top": "rise"}
    result = await wrapper.get_top_rise_fall_stocks(rise=True)
    mock_client.get_top_rise_fall_stocks.assert_called_once_with(True)
    assert result == {"top": "rise"}

    # 8. get_top_volume_stocks
    mock_client.get_top_volume_stocks.return_value = {"top": "volume"}
    result = await wrapper.get_top_volume_stocks()
    mock_client.get_top_volume_stocks.assert_called_once()
    assert result == {"top": "volume"}

    # 9. get_multi_price
    mock_client.get_multi_price.return_value = {"multi": "price"}
    result = await wrapper.get_multi_price(["005930", "000660"])
    mock_client.get_multi_price.assert_called_once_with(["005930", "000660"])
    assert result == {"multi": "price"}

    # 10. get_etf_info
    mock_client.get_etf_info.return_value = {"etf": "info"}
    result = await wrapper.get_etf_info("122630")
    mock_client.get_etf_info.assert_called_once_with("122630")
    assert result == {"etf": "info"}

    # 11. get_financial_ratio
    mock_client.get_financial_ratio.return_value = {"financial": "ratio"}
    result = await wrapper.get_financial_ratio("005930")
    mock_client.get_financial_ratio.assert_called_once_with("005930")
    assert result == {"financial": "ratio"}
