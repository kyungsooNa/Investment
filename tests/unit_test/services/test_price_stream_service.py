import asyncio
import pytest
import time
from unittest.mock import MagicMock
from services.price_stream_service import PriceStreamService
from services.data_quality_service import DataQualityResult


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
    assert service._last_tick_ts == {}
    assert service._last_any_tick_ts == 0.0


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
    assert price_stream_service.get_last_tick_ts('005930') == cached['received_at']
    assert price_stream_service.get_last_any_tick_ts() == cached['received_at']

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


def test_cache_price_snapshot_updates_cache_without_tick_tracking(price_stream_service, mock_stock_repo):
    """REST 스냅샷 현재가는 캐시를 채우되 웹소켓 틱 시각은 건드리지 않는다."""
    price_stream_service.cache_price_snapshot(
        "005930",
        price="71000",
        change="500",
        rate="0.71",
        sign="2",
        volume="3210",
    )

    cached = price_stream_service.get_cached_price("005930")
    assert cached is not None
    assert cached["price"] == "71000"
    assert cached["change"] == "500"
    assert cached["rate"] == "0.71"
    assert cached["sign"] == "2"
    assert price_stream_service.get_last_tick_ts("005930") == 0.0
    assert price_stream_service.get_last_any_tick_ts() == 0.0
    mock_stock_repo.update_realtime_data.assert_called_once_with("005930", 71000.0, 3210)


def test_on_price_tick_stores_liquidity_fields(price_stream_service):
    """체결틱의 누적거래량/누적거래대금이 snapshot 에 보관된다."""
    data = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '75000',
        '누적거래량': '1500000',
        '누적거래대금': '112500000000',
    }

    price_stream_service.on_price_tick(data)

    snap = price_stream_service.get_liquidity_snapshot('005930')
    assert snap is not None
    assert snap['acml_vol'] == 1500000
    assert snap['acml_tr_pbmn'] == 112500000000
    assert isinstance(snap['received_at'], float)


def test_on_price_tick_handles_missing_trading_value(price_stream_service):
    """누적거래대금 키가 없거나 N/A 인 경우 0 으로 보관된다."""
    data = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '75000',
        '누적거래량': '1500000',
        # 누적거래대금 누락
    }
    price_stream_service.on_price_tick(data)
    snap = price_stream_service.get_liquidity_snapshot('005930')
    assert snap is not None
    assert snap['acml_tr_pbmn'] == 0


def test_cache_price_snapshot_stores_acml_tr_pbmn(price_stream_service):
    """REST 스냅샷의 acml_tr_pbmn 값이 snapshot 에 반영된다."""
    price_stream_service.cache_price_snapshot(
        "005930",
        price="71000",
        volume="3210",
        acml_tr_pbmn="22791000",
    )
    snap = price_stream_service.get_liquidity_snapshot("005930")
    assert snap is not None
    assert snap['acml_vol'] == 3210
    assert snap['acml_tr_pbmn'] == 22791000


def test_get_liquidity_snapshot_returns_none_when_unknown(price_stream_service):
    assert price_stream_service.get_liquidity_snapshot("999999") is None


def test_on_price_tick_repo_exception(price_stream_service, mock_stock_repo, mock_logger):
    """레포지토리 업데이트 중 예외 발생 시 로거로 경고를 남기고 앱이 중단되지 않는지 검증"""
    data = {'유가증권단축종목코드': '005930', '주식현재가': '75000'}
    mock_stock_repo.update_realtime_data.side_effect = Exception("DB Connection Error")

    # 예외가 발생하더라도 상위로 전파되지 않아야 함
    price_stream_service.on_price_tick(data)

    mock_logger.warning.assert_called_once()
    assert "StockRepository 실시간 틱 캐시 갱신 실패: DB Connection Error" in mock_logger.warning.call_args[0][0]


# ── SSE 큐 관리 ──────────────────────────────────────────────────────────────

def test_init_sse_queues(mock_stock_repo, mock_logger):
    """_sse_queues 초기값이 빈 dict인지 검증"""
    service = PriceStreamService(stock_repo=mock_stock_repo, logger=mock_logger)
    assert service._sse_queues == {}


def test_create_subscriber_queue(price_stream_service):
    """큐 생성 후 해당 종목코드로 등록되는지 검증"""
    q = price_stream_service.create_subscriber_queue('005930')
    assert isinstance(q, asyncio.Queue)
    assert q in price_stream_service._sse_queues['005930']


def test_create_subscriber_queue_multiple(price_stream_service):
    """동일 종목에 복수의 큐 등록 가능한지 검증"""
    q1 = price_stream_service.create_subscriber_queue('005930')
    q2 = price_stream_service.create_subscriber_queue('005930')
    assert len(price_stream_service._sse_queues['005930']) == 2
    assert q1 in price_stream_service._sse_queues['005930']
    assert q2 in price_stream_service._sse_queues['005930']


def test_remove_subscriber_queue_cleans_up_empty_key(price_stream_service):
    """마지막 큐 제거 시 종목코드 항목이 삭제되는지 검증"""
    q = price_stream_service.create_subscriber_queue('005930')
    price_stream_service.remove_subscriber_queue('005930', q)
    assert '005930' not in price_stream_service._sse_queues


def test_remove_subscriber_queue_keeps_remaining(price_stream_service):
    """복수 큐 중 하나만 제거해도 나머지가 유지되는지 검증"""
    q1 = price_stream_service.create_subscriber_queue('005930')
    q2 = price_stream_service.create_subscriber_queue('005930')
    price_stream_service.remove_subscriber_queue('005930', q1)
    assert price_stream_service._sse_queues['005930'] == [q2]


def test_remove_subscriber_queue_nonexistent_no_error(price_stream_service):
    """등록되지 않은 큐 제거 시 예외 없이 무시되는지 검증"""
    q = asyncio.Queue()
    price_stream_service.remove_subscriber_queue('005930', q)  # should not raise


# ── SSE 브로드캐스트 ──────────────────────────────────────────────────────────

def test_on_price_tick_broadcasts_to_sse_queue(price_stream_service):
    """정상 틱 수신 시 SSE 큐에 데이터가 전달되는지 검증"""
    q = price_stream_service.create_subscriber_queue('005930')
    data = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '75000',
        '누적거래량': '1500000',
    }
    price_stream_service.on_price_tick(data)

    assert not q.empty()
    tick = q.get_nowait()
    assert tick == {
        "code": "005930",
        "price": 75000.0,
        "volume": 1500000,
        "change": "0",
        "rate": "0.00",
        "sign": "3",
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
    }


def test_on_price_tick_ignores_queue_full(price_stream_service):
    """SSE 큐가 가득 찼을 때 QueueFull 예외가 전파되지 않는지 검증"""
    q = asyncio.Queue(maxsize=1)
    q.put_nowait({"dummy": True})
    price_stream_service._sse_queues['005930'] = [q]

    data = {'유가증권단축종목코드': '005930', '주식현재가': '75000'}
    price_stream_service.on_price_tick(data)  # should not raise


def test_on_price_tick_no_sse_subscriber(price_stream_service):
    """SSE 구독자가 없는 종목 틱 수신 시 오류 없이 정상 처리되는지 검증"""
    data = {'유가증권단축종목코드': '005930', '주식현재가': '75000'}
    price_stream_service.on_price_tick(data)
    assert price_stream_service._sse_queues == {}


def test_on_price_tick_broadcasts_even_if_repo_fails(price_stream_service, mock_stock_repo):
    """레포지토리 업데이트 실패 후에도 SSE 큐 브로드캐스트가 수행되는지 검증"""
    mock_stock_repo.update_realtime_data.side_effect = Exception("DB Error")
    q = price_stream_service.create_subscriber_queue('005930')
    data = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '75000',
        '누적거래량': '1000',
    }
    price_stream_service.on_price_tick(data)

    assert not q.empty()
    tick = q.get_nowait()
    assert tick["price"] == 75000.0
    assert tick["volume"] == 1000


def test_mark_subscription_requested_and_get_subscription_age(price_stream_service):
    """구독 요청 시각이 기록되고 age 조회에 반영된다."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.price_stream_service.time.time", lambda: 1000.0)
        price_stream_service.mark_subscription_requested("005930")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.price_stream_service.time.time", lambda: 1015.0)
        assert price_stream_service.get_subscription_age("005930") == 15.0


def test_get_stale_codes_with_last_tick(price_stream_service):
    """마지막 틱 시각이 임계값을 넘은 종목만 stale로 판단한다."""
    price_stream_service._last_tick_ts["005930"] = 100.0
    price_stream_service._last_tick_ts["000660"] = 250.0

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.price_stream_service.time.time", lambda: 400.0)
        stale_codes = price_stream_service.get_stale_codes(180.0, codes=["005930", "000660"])

    assert stale_codes == ["005930"]


def test_get_stale_codes_with_no_tick_after_grace(price_stream_service):
    """아직 틱이 없더라도 구독 후 충분히 오래되면 stale로 판단한다."""
    price_stream_service._subscription_requested_ts["005930"] = 100.0
    price_stream_service._subscription_requested_ts["000660"] = 350.0

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.price_stream_service.time.time", lambda: 400.0)
        stale_codes = price_stream_service.get_stale_codes(180.0, codes=["005930", "000660"])

    assert stale_codes == ["005930"]


def test_clear_subscription_state_removes_cached_tracking(price_stream_service):
    """구독 해제 시 캐시/추적 상태가 정리된다."""
    price_stream_service._latest_prices["005930"] = {"price": "70000"}
    price_stream_service._last_tick_ts["005930"] = 100.0
    price_stream_service._subscription_requested_ts["005930"] = 50.0

    price_stream_service.clear_subscription_state("005930")

    assert price_stream_service.get_cached_price("005930") is None
    assert price_stream_service.get_last_tick_ts("005930") == 0.0
    assert price_stream_service.get_subscription_age("005930") == 0.0


def test_on_price_tick_quality_metadata_saved(mock_stock_repo, mock_logger):
    dq = MagicMock()
    dq.validate_price_tick.return_value = DataQualityResult(ok=True, reason="ok", latency_sec=0.25)
    svc = PriceStreamService(stock_repo=mock_stock_repo, logger=mock_logger, data_quality_service=dq)

    svc.on_price_tick({"유가증권단축종목코드": "005930", "주식현재가": "75000", "누적거래량": "1"})

    cached = svc.get_cached_price("005930")
    assert cached["quality_status"] == "ok"
    assert cached["quality_reason"] == "ok"
    assert cached["latency_sec"] == 0.25


def test_on_price_tick_invalid_quality_not_cached(mock_stock_repo, mock_logger):
    dq = MagicMock()
    dq.validate_price_tick.return_value = DataQualityResult(
        ok=False,
        severity="error",
        reason="invalid_tick",
        code="005930",
    )
    svc = PriceStreamService(stock_repo=mock_stock_repo, logger=mock_logger, data_quality_service=dq)

    svc.on_price_tick({"유가증권단축종목코드": "005930", "주식현재가": "0", "누적거래량": "1"})

    assert svc.get_cached_price("005930") is None
    mock_stock_repo.update_realtime_data.assert_not_called()
    mock_logger.warning.assert_called()
