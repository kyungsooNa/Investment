import pytest
import time
from unittest.mock import MagicMock
from services.price_stream_service import PriceStreamService


@pytest.fixture
def mock_stock_repo():
    return MagicMock()


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def price_stream_service(mock_stock_repo, mock_logger):
    return PriceStreamService(stock_repo=mock_stock_repo, logger=mock_logger)


def test_init(mock_stock_repo, mock_logger):
    """초기화 상태 검증"""
    service = PriceStreamService(stock_repo=mock_stock_repo, logger=mock_logger)
    assert service._stock_repo == mock_stock_repo
    assert service._logger == mock_logger
    assert service._latest_prices == {}


def test_on_price_tick_missing_code_or_price(price_stream_service, mock_stock_repo):
    """필수 필드(종목코드 또는 현재가)가 누락된 경우 조기 반환(return)되는지 검증"""
    # 종목코드 누락
    price_stream_service.on_price_tick({'주식현재가': '10000'})
    assert price_stream_service._latest_prices == {}
    mock_stock_repo.update_realtime_data.assert_not_called()

    # 현재가 누락
    price_stream_service.on_price_tick({'유가증권단축종목코드': '005930'})
    assert price_stream_service._latest_prices == {}
    mock_stock_repo.update_realtime_data.assert_not_called()


def test_on_price_tick_success(price_stream_service, mock_stock_repo):
    """모든 필드가 정상적으로 들어왔을 때 캐시 갱신 및 레포지토리 반영 검증"""
    data = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '75000',
        '전일대비': '1000',
        '전일대비율': '1.35',
        '전일대비부호': '2',
        '누적거래량': '1500000'
    }

    price_stream_service.on_price_tick(data)

    # 1. 내부 캐시 확인
    cached = price_stream_service.get_cached_price('005930')
    assert cached is not None
    assert cached['price'] == '75000'
    assert cached['change'] == '1000'
    assert cached['rate'] == '1.35'
    assert cached['sign'] == '2'
    assert 'received_at' in cached
    assert isinstance(cached['received_at'], float)

    # 2. StockRepository.update_realtime_data 호출 확인
    mock_stock_repo.update_realtime_data.assert_called_once_with('005930', 75000.0, 1500000)


def test_on_price_tick_defaults(price_stream_service, mock_stock_repo):
    """선택적 필드가 누락되거나 'N/A'일 때 기본값으로 갱신되는지 검증"""
    data = {
        '유가증권단축종목코드': '000660',
        '주식현재가': '150000',
        '누적거래량': 'N/A'  # N/A 문자열 처리 검증
        # 나머지 전일대비 등은 누락됨
    }

    price_stream_service.on_price_tick(data)

    cached = price_stream_service.get_cached_price('000660')
    assert cached['change'] == '0'
    assert cached['rate'] == '0.00'
    assert cached['sign'] == '3'

    mock_stock_repo.update_realtime_data.assert_called_once_with('000660', 150000.0, 0)


def test_on_price_tick_repo_exception(price_stream_service, mock_stock_repo, mock_logger):
    """레포지토리 업데이트 중 예외 발생 시 로거로 경고를 남기고 앱이 중단되지 않는지 검증"""
    data = {'유가증권단축종목코드': '005930', '주식현재가': '75000'}
    mock_stock_repo.update_realtime_data.side_effect = Exception("DB Connection Error")

    # 예외가 발생하더라도 상위로 전파되지 않아야 함
    price_stream_service.on_price_tick(data)

    mock_logger.warning.assert_called_once()
    assert "StockRepository 실시간 틱 캐시 갱신 실패: DB Connection Error" in mock_logger.warning.call_args[0][0]