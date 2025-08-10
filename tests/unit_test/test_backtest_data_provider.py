import pytest
from unittest.mock import AsyncMock, patch
from strategies.backtest_data_provider import BacktestDataProvider
import logging
from datetime import datetime
from common.types import ResCommonResponse


# Mock TimeManager 클래스 정의
class MockTimeManager:
    """테스트를 위해 고정된 시간을 반환하는 모의 TimeManager."""

    def get_current_kst_time(self):
        return datetime(2025, 1, 1, 9, 0, 0)  # 테스트를 위한 고정된 시간


@pytest.fixture
def backtest_data_provider_fixture():
    """BacktestDataProvider 인스턴스를 위한 픽스처."""
    trading_service = AsyncMock()
    mock_time_manager = MockTimeManager()
    # 로거는 테스트 출력을 깔끔하게 하기 위해 None으로 설정하거나, 필요시 목업
    return BacktestDataProvider(trading_service=trading_service, time_manager=mock_time_manager)


@pytest.fixture(autouse=True)
def caplog_fixture(caplog):
    """테스트 중 로깅 메시지를 캡처하고 기본 로깅 레벨을 설정합니다."""
    caplog.set_level(logging.INFO)  # 기본적으로 INFO 레벨 이상의 메시지를 캡처


@pytest.mark.asyncio
async def test_mock_price_lookup_exception_handling(backtest_data_provider_fixture, caplog):
    """
    mock_price_lookup에서 broker.get_price_summary가 예외를 발생시킬 때
    오류를 올바르게 처리하고 경고를 로깅하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    provider.trading_service.get_price_summary.side_effect = Exception("API 호출 실패")

    with caplog.at_level(logging.WARNING):
        result = await provider.mock_price_lookup("005930")

    assert result == 0
    assert "005930 모의 가격 조회 실패" in caplog.text


@pytest.mark.asyncio
async def test_mock_price_lookup_invalid_current_data(backtest_data_provider_fixture, caplog):
    """
    mock_price_lookup에서 broker.get_price_summary가 'current' 키가 없는
    데이터를 반환할 때를 테스트합니다. (경고 로그는 발생하지 않음)
    """
    provider = backtest_data_provider_fixture
    # 'current' 키가 없는 경우
    provider.trading_service.get_price_summary.return_value = {"symbol": "005930", "open": 70000}

    with caplog.at_level(logging.WARNING):  # 경고 로그가 발생하는지 확인하지만, 이 테스트에서는 발생하지 않음
        result = await provider.mock_price_lookup("005930")

    # get("current", 0) -> int(0 * 1.05) = 0. 예외가 발생하지 않으므로 로그는 남지 않음.
    assert result == 0


@pytest.mark.asyncio
async def test_mock_price_lookup_successful(backtest_data_provider_fixture):
    """
    mock_price_lookup이 유효한 현재 가격으로 올바르게 모의 가격을 계산하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    mock_current_price = 10000  # 가상 현재 가격
    provider.trading_service.get_price_summary.return_value = ResCommonResponse(
        rt_cd="0",  # 성공 코드
        msg1="성공",
        data={"symbol": "005930", "current": mock_current_price}
    )

    expected_price = int(mock_current_price * 1.05)

    result = await provider.mock_price_lookup("005930")

    assert result == expected_price
    provider.trading_service.get_price_summary.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_realistic_price_lookup_empty_or_invalid_chart_data(backtest_data_provider_fixture, caplog):
    """
    realistic_price_lookup에서 분봉 데이터가 없거나 형식이 올바르지 않을 때
    기준 시점의 현재 가격을 반환하고 경고를 로깅하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    provider.trading_service.inquire_daily_itemchartprice.return_value = []  # 빈 리스트

    base_summary = {"current": 77000}

    with caplog.at_level(logging.WARNING):
        result = await provider.realistic_price_lookup("005930", base_summary, 10)

    assert result == base_summary["current"]
    assert "005930의 분봉 데이터가 없거나 형식이 올바르지 않습니다." in caplog.text

    # chart_data가 None인 경우 테스트
    caplog.clear()
    provider.trading_service.inquire_daily_itemchartprice.return_value = None
    with caplog.at_level(logging.WARNING):
        result = await provider.realistic_price_lookup("005930", base_summary, 10)
    assert result == base_summary["current"]
    assert "005930의 분봉 데이터가 없거나 형식이 올바르지 않습니다." in caplog.text

    # chart_data가 리스트가 아닌 경우 테스트
    caplog.clear()
    provider.trading_service.inquire_daily_itemchartprice.return_value = "invalid_data"
    with caplog.at_level(logging.WARNING):
        result = await provider.realistic_price_lookup("005930", base_summary, 10)
    assert result == base_summary["current"]
    assert "005930의 분봉 데이터가 없거나 형식이 올바르지 않습니다." in caplog.text


@pytest.mark.asyncio
async def test_realistic_price_lookup_base_price_not_found(backtest_data_provider_fixture, caplog):
    """
    realistic_price_lookup에서 기준 시점의 가격을 분봉 데이터에서 찾지 못할 때
    기준 시점의 가격을 반환하고 경고를 로깅하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    # 기준 가격인 77000이 차트 데이터에 없는 경우
    provider.trading_service.inquire_daily_itemchartprice.return_value = [
        {'stck_clpr': '76000'},
        {'stck_clpr': '78000'}
    ]
    base_summary = {"current": 77000}

    with caplog.at_level(logging.WARNING):
        result = await provider.realistic_price_lookup("005930", base_summary, 10)

    assert result == base_summary["current"]
    assert "005930의 기준 시점 분봉(77000)을 찾지 못했습니다." in caplog.text


@pytest.mark.asyncio
async def test_realistic_price_lookup_after_index_less_than_zero(backtest_data_provider_fixture):
    """
    realistic_price_lookup에서 minutes_after로 인해 after_index가 0 미만이 될 때
    첫 번째 분봉 가격을 올바르게 반환하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    provider.trading_service.inquire_daily_itemchartprice.return_value = [
        {'stck_clpr': '70000'},  # after_index가 0이 됨
        {'stck_clpr': '71000'},
        {'stck_clpr': '72000'}  # base_price
    ]
    base_summary = {"current": 72000}  # base_index = 2

    # base_index(2) - minutes_after(5) = -3. 이 경우 after_index는 0이 되어야 함.
    result = await provider.realistic_price_lookup("005930", base_summary, 5)

    assert result == 70000  # 첫 번째 분봉 가격


@pytest.mark.asyncio
async def test_realistic_price_lookup_after_index_greater_than_length(backtest_data_provider_fixture):
    """
    realistic_price_lookup에서 minutes_after로 인해 after_index가
    차트 데이터 길이를 초과할 때 마지막 분봉 가격을 올바르게 반환하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    provider.trading_service.inquire_daily_itemchartprice.return_value = [
        {'stck_clpr': '70000'},  # base_price
        {'stck_clpr': '71000'},  # after_index가 len-1이 됨
        {'stck_clpr': '72000'}
    ]
    base_summary = {"current": 70000}  # base_index = 0

    # base_index(0) - minutes_after(-5) = 5. 이 경우 after_index는 len-1이 되어야 함.
    # minutes_after가 음수일 때 after_index가 리스트 길이를 초과하는 경우를 상정합니다.
    # (일반적으로 minutes_after는 양수이나, 코드 로직의 견고성을 위해 테스트)
    result = await provider.realistic_price_lookup("005930", base_summary, -5)  # minutes_after = -5

    assert result == 72000  # 마지막 분봉 가격


@pytest.mark.asyncio
async def test_realistic_price_lookup_api_call_exception(backtest_data_provider_fixture, caplog):
    """
    realistic_price_lookup에서 broker.inquire_daily_itemchartprice가 예외를 발생시킬 때
    오류를 처리하고 에러를 로깅하며 기준 시점 가격을 반환하는지 테스트합니다.
    """
    provider = backtest_data_provider_fixture
    provider.trading_service.inquire_daily_itemchartprice.side_effect = Exception("분봉 데이터 API 오류")

    base_summary = {"current": 77000}

    with caplog.at_level(logging.ERROR):  # ERROR 레벨 로깅 확인
        result = await provider.realistic_price_lookup("005930", base_summary, 10)

    assert result == base_summary["current"]
    assert "005930 가격 조회 중 오류 발생: 분봉 데이터 API 오류" in caplog.text