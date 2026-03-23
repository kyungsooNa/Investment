import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
import pytz
from services.market_calendar_service import MarketCalendarService
from common.types import ResCommonResponse

@pytest.fixture
def mock_deps():
    tm = MagicMock()
    # Default time
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    
    # MarketClock 속성 및 메서드 모킹 추가 (get_next_open_time 테스트용)
    tm.market_open_time_str = "09:00"
    tm.market_close_time_str = "15:30"   # ✅ 이 줄을 반드시 추가해 주세요!
    tm.market_timezone = pytz.timezone("Asia/Seoul")
    
    def _get_market_open_time():
        now = tm.get_current_kst_time()
        if now.tzinfo is None:
            now = tm.market_timezone.localize(now)
        return tm.market_timezone.localize(datetime(now.year, now.month, now.day, 9, 0, 0))
    tm.get_market_open_time.side_effect = _get_market_open_time
    
    def _get_market_close_time():
        now = tm.get_current_kst_time()
        if now.tzinfo is None:
            now = tm.market_timezone.localize(now)
        return tm.market_timezone.localize(datetime(now.year, now.month, now.day, 15, 30, 0))
    tm.get_market_close_time.side_effect = _get_market_close_time

    logger = MagicMock()
    return tm, logger

@pytest.fixture
def manager(mock_deps):
    tm, logger = mock_deps
    return MarketCalendarService(tm, logger)

@pytest.fixture
def mock_broker():
    broker = MagicMock()
    # Mocking the structure: broker._client._quotations._client or broker._client._quotations
    
    # Case 1: With ClientWithCache wrapper
    # broker._client (KoreaInvestApiClient)
    #   ._quotations (ClientWithCache)
    #     ._client (KoreaInvestApiQuotations) -> This is what we need to mock inquire_daily_itemchartprice on
    
    raw_quotations = AsyncMock()
    
    quotations_wrapper = MagicMock()
    quotations_wrapper._client = raw_quotations
    
    kis_client = MagicMock()
    kis_client._quotations = quotations_wrapper
    
    # BrokerAPIWrapper wraps the client.
    # broker._client is the wrapper.
    # wrapper._client is the actual KoreaInvestApiClient (kis_client).
    client_wrapper = MagicMock()
    client_wrapper._client = kis_client
    broker._client = client_wrapper
    
    return broker, raw_quotations

@pytest.mark.asyncio
async def test_get_latest_trading_date_cached(manager, mock_deps):
    tm, _ = mock_deps
    # Setup cache
    manager._cached_date = "20250101"
    manager._last_check_date = "20250101"
    
    # Current date matches last check date
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 15, 0, 0)
    
    result = await manager.get_latest_trading_date()
    assert result == "20250101"
    # Broker should not be accessed if cached
    assert manager._broker is None 

@pytest.mark.asyncio
async def test_get_latest_trading_date_no_broker(manager, mock_deps):
    tm, logger = mock_deps
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    result = await manager.get_latest_trading_date()
    
    assert result is None
    logger.warning.assert_called_with("MarketCalendarService: Broker is not set.")

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_latest_trading_date_api_success(manager, mock_deps, mock_broker):
    tm, logger = mock_deps
    broker, _ = mock_broker  # raw_quotations는 더 이상 사용하지 않음
    
    manager.set_broker(broker)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    # 상위 broker 객체의 API 호출 메서드를 모킹!
    broker.inquire_daily_itemchartprice = AsyncMock()
    mock_resp = ResCommonResponse(
        rt_cd="0", msg1="OK", 
        data=[
            {"stck_bsop_date": "20250102"},
            {"stck_bsop_date": "20241231"}
        ]
    )
    broker.inquire_daily_itemchartprice.return_value = mock_resp
    
    result = await manager.get_latest_trading_date()
    
    assert result == "20250102"
    assert manager._cached_date == "20250102"
    assert manager._last_check_date == "20250102"
    
    # 키워드 인자(kwargs)로 정상적으로 호출되었는지 검증
    broker.inquire_daily_itemchartprice.assert_awaited_once_with(
        stock_code="005930",
        start_date="20241226",
        end_date="20250102",
        fid_period_div_code="D",
        _skip_cache=True
    )

@pytest.mark.asyncio
async def test_get_latest_trading_date_api_fail(manager, mock_deps, mock_broker):
    tm, logger = mock_deps
    broker, _ = mock_broker
    
    manager.set_broker(broker)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    broker.inquire_daily_itemchartprice = AsyncMock()
    mock_resp = ResCommonResponse(rt_cd="1", msg1="Fail", data=None)
    broker.inquire_daily_itemchartprice.return_value = mock_resp
    
    result = await manager.get_latest_trading_date()
    
    assert result is None
    assert manager._cached_date is None

@pytest.mark.asyncio
async def test_get_latest_trading_date_exception(manager, mock_deps, mock_broker):
    tm, logger = mock_deps
    broker, _ = mock_broker
    
    manager.set_broker(broker)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 12, 0, 0)
    
    broker.inquire_daily_itemchartprice = AsyncMock(side_effect=Exception("API Error"))
    
    result = await manager.get_latest_trading_date()
    
    assert result is None
    logger.error.assert_called() # 예외 발생 로깅 확인

def test_init_default_logger(mock_deps):
    """로거 없이 초기화 시 기본 로거 생성 확인"""
    tm, _ = mock_deps
    manager = MarketCalendarService(tm)
    assert manager._logger is not None


@pytest.mark.asyncio
async def test_fetch_from_api_empty_data(manager, mock_deps, mock_broker):
    """API 응답 데이터가 비어있을 때"""
    tm, logger = mock_deps
    broker, _ = mock_broker
    manager.set_broker(broker)
    
    broker.inquire_daily_itemchartprice = AsyncMock()
    mock_resp = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    broker.inquire_daily_itemchartprice.return_value = mock_resp
    
    result = await manager.get_latest_trading_date()
    assert result is None

@pytest.mark.asyncio
async def test_get_latest_trading_date_outer_exception(manager, mock_deps):
    """get_latest_trading_date 내부 try-except 블록 테스트"""
    tm, logger = mock_deps
    
    # Broker 설정 (초기 체크 통과용)
    manager.set_broker(MagicMock())
    
    # _fetch_from_api가 예외를 던지도록 모킹 (내부 예외 처리가 아닌 외부 예외 처리 테스트)
    with patch.object(manager, '_fetch_from_api', side_effect=Exception("Critical Error")):
        result = await manager.get_latest_trading_date()
        
        assert result is None
        logger.error.assert_called()
        assert "최근 영업일 조회 실패" in logger.error.call_args[0][0]

@pytest.mark.asyncio
async def test_is_business_day_cache_logic(manager, mock_deps, mock_broker):
    """is_business_day 호출 시 API 1회 호출 후 캐싱되는지 테스트"""
    tm, logger = mock_deps
    broker, _ = mock_broker  # 불필요한 내부 변수(_) 처리
    manager.set_broker(broker)
    
    # 한투 '국내휴장일조회' API 응답 Mocking (ResCommonResponse 객체로 감싸기)
    mock_resp = ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={
            "output": [
                {"bass_dt": "20250101", "bzdy_yn": "N", "tr_day_yn": "N"},
                {"bass_dt": "20250102", "bzdy_yn": "Y", "tr_day_yn": "Y"}
            ]
        }
    )
    broker.check_holiday = AsyncMock(return_value=mock_resp)
    
    # 1. 20250102 조회 (처음이므로 API 호출됨)
    is_open = await manager.is_business_day("20250102")
    assert is_open is True
    broker.check_holiday.assert_awaited_once() # API 1회 호출 확인
    
    # 2. 20250101 조회 (같은 월이므로 API 호출 없이 캐시에서 가져옴)
    is_open_holiday = await manager.is_business_day("20250101")
    assert is_open_holiday is False
    broker.check_holiday.assert_awaited_once() # 호출 횟수 여전히 1회 확인

@pytest.mark.asyncio
async def test_is_market_open_now(manager, mock_deps):
    """is_market_open_now 통합 검증 테스트"""
    tm, logger = mock_deps
    
    # 1. 영업일이면서 시간대도 맞을 때
    manager.is_business_day = AsyncMock(return_value=True)
    tm.is_market_operating_hours = MagicMock(return_value=True)
    assert await manager.is_market_open_now() is True
    
    # 2. 영업일이지만 장이 끝났을 때
    tm.is_market_operating_hours = MagicMock(return_value=False)
    assert await manager.is_market_open_now() is False
    
    # 3. 시간은 평일 10시지만 공휴일(영업일 아님)일 때
    manager.is_business_day = AsyncMock(return_value=False)
    tm.is_market_operating_hours = MagicMock(return_value=True) # 시계는 장중이라고 판단
    assert await manager.is_market_open_now() is False # 달력이 컷트!

# ── get_next_open_time Tests ──────────────────────────────────────────

def _is_weekday_side_effect(date_str):
    """YYYYMMDD 문자열이 평일(월~금)인지 확인"""
    dt = datetime.strptime(date_str, "%Y%m%d")
    return dt.weekday() < 5

@pytest.mark.asyncio
async def test_get_next_open_time_before_today_open(manager, mock_deps):
    """
    현재 시간이 오늘 개장 시간 이전인 경우
    """
    tm, logger = mock_deps
    # 금요일 08:00
    target_dt = datetime(2025, 6, 27, 8, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)
    
    # 평일 체크 모킹
    manager.is_business_day = AsyncMock(side_effect=_is_weekday_side_effect)

    next_open = await manager.get_next_open_time()

    expected_open_time = tm.market_timezone.localize(datetime(2025, 6, 27, 9, 0, 0))  # 오늘 9시
    assert next_open == expected_open_time

@pytest.mark.asyncio
async def test_get_next_open_time_after_today_open_weekday(manager, mock_deps):
    """현재 시간이 오늘 개장 시간 이후 평일 (폐장 후)"""
    tm, logger = mock_deps
    target_dt = datetime(2025, 6, 27, 16, 0, 0) # 금요일 16:00
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)

    manager.is_business_day = AsyncMock(side_effect=_is_weekday_side_effect)
    
    # ✅ 누락되었던 다음 영업일 조회(월요일) 모킹 추가!
    manager.get_next_open_day = AsyncMock(return_value="20250630")

    next_open = await manager.get_next_open_time()
    expected_open_time = tm.market_timezone.localize(datetime(2025, 6, 30, 9, 0, 0))  # 다음주 월요일 9시
    assert next_open == expected_open_time

@pytest.mark.asyncio
async def test_get_next_open_time_after_today_open_saturday(manager, mock_deps):
    """현재 시간이 토요일"""
    tm, logger = mock_deps
    target_dt = datetime(2025, 6, 28, 10, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)

    manager.is_business_day = AsyncMock(side_effect=_is_weekday_side_effect)
    
    # ✅ 모킹 추가
    manager.get_next_open_day = AsyncMock(return_value="20250630")

    next_open = await manager.get_next_open_time()
    expected_open_time = tm.market_timezone.localize(datetime(2025, 6, 30, 9, 0, 0))
    assert next_open == expected_open_time

@pytest.mark.asyncio
async def test_get_next_open_time_after_today_open_sunday(manager, mock_deps):
    """현재 시간이 일요일"""
    tm, logger = mock_deps
    target_dt = datetime(2025, 6, 29, 10, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)

    manager.is_business_day = AsyncMock(side_effect=_is_weekday_side_effect)
    
    # ✅ 모킹 추가
    manager.get_next_open_day = AsyncMock(return_value="20250630")

    next_open = await manager.get_next_open_time()
    expected_open_time = tm.market_timezone.localize(datetime(2025, 6, 30, 9, 0, 0))
    assert next_open == expected_open_time

@pytest.mark.asyncio
async def test_get_next_open_time_saturday_morning(manager, mock_deps):
    """토요일 아침 (개장 시간 전)"""
    tm, logger = mock_deps
    target_dt = datetime(2025, 6, 28, 8, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)

    manager.is_business_day = AsyncMock(side_effect=_is_weekday_side_effect)
    
    # ✅ 모킹 추가
    manager.get_next_open_day = AsyncMock(return_value="20250630")

    next_open = await manager.get_next_open_time()
    expected_open = tm.market_timezone.localize(datetime(2025, 6, 30, 9, 0, 0))
    assert next_open == expected_open

# [추가할 코드]
@pytest.mark.asyncio
async def test_get_latest_market_close_time_weekday(manager, mock_deps):
    """평일 기준으로 직전 마감 시간 반환 테스트"""
    tm, logger = mock_deps
    
    # 월요일 오전 8시 기준: 직전 금요일 15:30 반환해야 함
    target_dt = datetime(2025, 8, 4, 8, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)
    
    # 평일 체크 모킹
    manager.is_business_day = AsyncMock(side_effect=_is_weekday_side_effect)

    latest_close = await manager.get_latest_market_close_time()

    assert latest_close.weekday() == 4  # 금요일(4)
    assert latest_close.hour == 15
    assert latest_close.minute == 30
    assert latest_close < tm.get_current_kst_time()

@pytest.mark.asyncio
async def test_get_latest_market_close_time_today_after_close(manager, mock_deps):
    """오늘이 영업일이고 이미 장이 마감된 상태(15:30 이후)일 때 오늘 마감 시간 반환 테스트"""
    tm, logger = mock_deps
    
    # 오늘 16시로 설정 (장 마감 이후)
    target_dt = datetime(2025, 8, 4, 16, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)
    
    # 평일 체크 모킹 (오늘은 영업일)
    manager.is_business_day = AsyncMock(return_value=True)

    latest_close = await manager.get_latest_market_close_time()

    assert latest_close.year == 2025
    assert latest_close.month == 8
    assert latest_close.day == 4
    assert latest_close.hour == 15
    assert latest_close.minute == 30

@pytest.mark.asyncio
async def test_get_latest_market_close_time_no_business_day_in_15_days(manager, mock_deps):
    """최근 15일 내에 영업일이 없는 경우 (시스템 오류 의심) None 반환 테스트"""
    tm, logger = mock_deps
    target_dt = datetime(2025, 8, 4, 10, 0, 0)
    tm.get_current_kst_time.return_value = tm.market_timezone.localize(target_dt)
    
    # 계속 휴장일이라 가정
    manager.is_business_day = AsyncMock(return_value=False)

    latest_close = await manager.get_latest_market_close_time()

    assert latest_close is None
    logger.error.assert_called()

@pytest.mark.asyncio
async def test_get_next_open_day_no_args(manager, mock_deps):
    """get_next_open_day 파라미터 없이 호출 시 현재 시간 기준 다음 영업일 반환 테스트"""
    tm, logger = mock_deps
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0) # 수요일
    
    manager._sync_calendar_if_needed = AsyncMock()
    manager._business_days_cache = {"20250102": True} # 다음날 영업일로 캐시 조작

    result = await manager.get_next_open_day()
    assert result == "20250102"
    
@pytest.mark.asyncio
async def test_get_next_open_day_with_args(manager, mock_deps):
    """get_next_open_day 특정 기준일 입력 시 연휴 건너뛰고 영업일 반환 테스트"""
    manager._sync_calendar_if_needed = AsyncMock()
    # 20250103(금) 기준. 주말 건너뛰고 20250106(월)로 이동하는지 검증
    manager._business_days_cache = {"20250106": True}
    
    result = await manager.get_next_open_day("20250103")
    assert result == "20250106"

@pytest.mark.asyncio
async def test_get_next_open_day_no_business_day_found(manager, mock_deps):
    """15일간 탐색했음에도 영업일이 없을 경우 원본 날짜를 반환하는지 테스트"""
    manager._sync_calendar_if_needed = AsyncMock()
    manager._business_days_cache = {} # 캐시에 영업일 없음 (계속 False 반환)
    
    result = await manager.get_next_open_day("20250101")
    assert result == "20250101"

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_wait_until_next_open_no_sleep(mock_sleep, manager, mock_deps):
    """장 중일 때 wait_until_next_open 호출 시 sleep 없이 바로 통과하는지 테스트"""
    tm, logger = mock_deps
    # 현재 시간이 다음 오픈 시간보다 크거나 같을 때 대기 안 함을 보장
    manager.get_next_open_time = AsyncMock(return_value=tm.get_current_kst_time.return_value)
    await manager.wait_until_next_open()
    mock_sleep.assert_not_awaited()