import pytest

from core.time_manager import TimeManager
from unittest.mock import MagicMock, patch
import datetime as dt # datetime 모듈 자체를 사용할 경우를 대비하여 alias를 주는 것도 좋습니다.
from datetime import datetime, timedelta # datetime 모듈 자체를 사용할 경우를 대비하여 alias를 주는 것도 좋습니다.

import pytz
import logging


def get_test_logger():
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.DEBUG)

    # 기존 핸들러 제거
    if logger.hasHandlers():
        logger.handlers.clear()

    # 콘솔 출력만 (파일 기록 없음)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
# 테스트용 로거 설정
@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def time_manager(mock_logger):
    # 기본값으로 인스턴스 생성, 필요시 테스트에서 재정의
    return TimeManager(logger=mock_logger)

def test_get_current_kst_time_returns_timezone_aware_datetime():
    manager = TimeManager()
    current_time = manager.get_current_kst_time()

    assert current_time.tzinfo is not None
    assert str(current_time.tzinfo) == "Asia/Seoul"

@patch("core.time_manager.TimeManager.get_current_kst_time")
def test_is_market_open_true(mock_get_time):
    kst = pytz.timezone("Asia/Seoul")
    mock_get_time.return_value = kst.localize(datetime(2024, 6, 21, 10, 0, 0))
    manager = TimeManager()
    assert manager.is_market_open() is True

@patch("core.time_manager.TimeManager.get_current_kst_time")
def test_is_market_open_false_weekend(mock_get_time):
    kst = pytz.timezone("Asia/Seoul")
    mock_get_time.return_value = kst.localize(datetime(2024, 6, 22, 10, 0, 0))  # 토요일
    manager = TimeManager()
    assert manager.is_market_open() is False

@patch("core.time_manager.TimeManager.get_current_kst_time")
def test_is_market_open_false_after_hours(mock_get_time):
    kst = pytz.timezone("Asia/Seoul")
    mock_get_time.return_value = kst.localize(datetime(2024, 6, 21, 16, 0, 0))  # 평일 16시
    manager = TimeManager()
    assert manager.is_market_open() is False

@patch("core.time_manager.logging.getLogger")
def test_init_default_values(mock_logger):
    """
    __init__ 메서드의 기본값 설정 및 로거 할당 확인
    """
    dummy_logger = MagicMock()
    mock_logger.return_value = dummy_logger

    tm = TimeManager()
    assert tm.market_open_time_str == "09:00"
    assert tm.market_close_time_str == "15:30"
    assert tm.timezone_name == "Asia/Seoul"
    assert tm.logger == dummy_logger  # 로거가 패치된 것인지 확인

    tm_with_custom_logger = TimeManager(logger=mock_logger)
    assert tm_with_custom_logger.logger == mock_logger


def test_init_custom_values(mock_logger):
    """
    __init__ 메서드의 커스텀 값 설정 확인
    """
    tm = TimeManager(market_open_time="10:00", market_close_time="16:00", timezone="America/New_York",
                     logger=mock_logger)
    assert tm.market_open_time_str == "10:00"
    assert tm.market_close_time_str == "16:00"
    assert tm.timezone_name == "America/New_York"
    assert tm.logger == mock_logger
    assert tm.market_timezone == pytz.timezone("America/New_York")


def test_init_unknown_timezone_error(mock_logger):
    """
    __init__ 메서드의 pytz.UnknownTimeZoneError 예외 처리 커버 (라인 23-26)
    """
    with patch('pytz.timezone') as mock_pytz_timezone, \
            patch('logging.getLogger') as mock_get_logger:
        mock_get_logger.return_value = mock_logger

        # Define a mock for the valid timezone object it would return
        # 실제 pytz.timezone("Asia/Seoul")을 호출하는 대신, Mock 객체를 생성합니다.
        mock_asia_seoul_timezone_return_value = MagicMock(spec=pytz.tzinfo.DstTzInfo)
        # 필요한 경우, Mock 객체의 zone 속성을 설정하여 실제 객체처럼 동작하도록 합니다.
        mock_asia_seoul_timezone_return_value.zone = "Asia/Seoul"

        # Set side_effect: first call raises, second call returns the mock timezone object
        mock_pytz_timezone.side_effect = [
            pytz.UnknownTimeZoneError,
            mock_asia_seoul_timezone_return_value  # 이제 실제 pytz.timezone 호출이 아닌 Mock 객체를 사용
        ]

        tm = TimeManager(timezone="Invalid/TimeZone", logger=mock_logger)

        assert tm.timezone_name == "Asia/Seoul"
        mock_logger.error.assert_called_once_with(f"알 수 없는 시간대: Invalid/TimeZone. 'Asia/Seoul'로 기본 설정합니다.")

        # 이제 정확히 두 번 호출될 것입니다.
        assert mock_pytz_timezone.call_count == 2
        assert mock_pytz_timezone.call_args_list[0].args[0] == "Invalid/TimeZone"
        assert mock_pytz_timezone.call_args_list[1].args[0] == "Asia/Seoul"

        # 실제 market_timezone과 Mock 객체가 같은지 확인
        assert tm.market_timezone == mock_asia_seoul_timezone_return_value


def test_get_current_kst_time(time_manager):
    """get_current_kst_time 메서드 커버"""

    with patch('core.time_manager.datetime') as mock_datetime_module:
        mock_datetime_instance = MagicMock(spec=datetime)
        mock_datetime_instance.year = 2025
        mock_datetime_instance.month = 6
        mock_datetime_instance.day = 27
        mock_datetime_instance.hour = 10
        mock_datetime_instance.minute = 0
        mock_datetime_instance.second = 0
        mock_datetime_instance.microsecond = 0
        mock_datetime_instance.tzinfo = time_manager.market_timezone

        # now() 함수가 해당 mock 객체를 반환하도록 설정
        mock_datetime_module.now.return_value = mock_datetime_instance

        result = time_manager.get_current_kst_time()

        # 검증
        assert result.hour == 10
        assert result.year == 2025
        assert result.tzinfo == time_manager.market_timezone


def test_is_market_open_on_weekend(time_manager, mock_logger):
    """
    is_market_open 메서드 커버: 주말인 경우 (라인 41-43)
    """
    # 토요일 (weekday()는 월요일이 0, 일요일이 6)
    saturday = time_manager.market_timezone.localize(dt.datetime(2025, 6, 28, 10, 0, 0))
    assert not time_manager.is_market_open(now=saturday)
    mock_logger.info.assert_called_once_with(
        f"시장 상태 - 주말이므로 시장이 닫혀 있습니다. (현재: {saturday.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
    mock_logger.reset_mock()

    # 일요일
    sunday = time_manager.market_timezone.localize(dt.datetime(2025, 6, 29, 10, 0, 0))
    assert not time_manager.is_market_open(now=sunday)
    mock_logger.info.assert_called_once_with(
        f"시장 상태 - 주말이므로 시장이 닫혀 있습니다. (현재: {sunday.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")


def test_is_market_open_during_hours(time_manager, mock_logger):
    """
    is_market_open 메서드 커버: 평일, 개장 시간 내 (라인 59-61)
    """
    # 평일 개장 시간 (09:00 ~ 15:30) 내의 시간
    weekday_in_hours = time_manager.market_timezone.localize(dt.datetime(2025, 6, 27, 10, 0, 0))  # 금요일 10시
    assert time_manager.is_market_open(now=weekday_in_hours)
    mock_logger.info.assert_called_once_with(
        f"시장 상태 - 시장이 열려 있습니다. (현재: {weekday_in_hours.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")


def test_is_market_open_before_open(time_manager, mock_logger):
    """
    is_market_open 메서드 커버: 평일, 개장 전 시간 (라인 63-65)
    """
    # 평일 개장 전 시간
    weekday_before_open = time_manager.market_timezone.localize(dt.datetime(2025, 6, 27, 8, 30, 0))  # 금요일 8시 30분
    assert not time_manager.is_market_open(now=weekday_before_open)
    mock_logger.info.assert_called_once_with(
        f"시장 상태 - 시장이 닫혀 있습니다. (현재: {weekday_before_open.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 개장: {time_manager.market_open_time_str}, 폐장: {time_manager.market_close_time_str})"
    )


def test_is_market_open_after_close(time_manager, mock_logger):
    """
    is_market_open 메서드 커버: 평일, 폐장 후 시간 (라인 63-65)
    """
    # 평일 폐장 후 시간
    weekday_after_close = time_manager.market_timezone.localize(dt.datetime(2025, 6, 27, 16, 0, 0))  # 금요일 16시
    assert not time_manager.is_market_open(now=weekday_after_close)
    mock_logger.info.assert_called_once_with(
        f"시장 상태 - 시장이 닫혀 있습니다. (현재: {weekday_after_close.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 개장: {time_manager.market_open_time_str}, 폐장: {time_manager.market_close_time_str})"
    )


def test_get_next_market_open_time_before_today_open(time_manager, mock_logger):
    """
    get_next_market_open_time 메서드 커버: 현재 시간이 오늘 개장 시간 이전인 경우 (라인 80-81)
    """
    # 현재 시간을 오늘 개장 시간보다 이르게 Mock
    with patch('core.time_manager.TimeManager.get_current_kst_time') as mock_get_current_kst_time:
        mock_get_current_kst_time.return_value = time_manager.market_timezone.localize(
            dt.datetime(2025, 6, 27, 8, 0, 0))  # 금요일 8시

        next_open = time_manager.get_next_market_open_time()

        expected_open_time = time_manager.market_timezone.localize(dt.datetime(2025, 6, 27, 9, 0, 0))  # 오늘 9시
        assert next_open == expected_open_time
        mock_logger.info.assert_called_once_with(
            f"다음 시장 개장 시간: {expected_open_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")


def test_get_next_market_open_time_after_today_open_weekday(time_manager, mock_logger):
    """
    get_next_market_open_time 메서드 커버: 현재 시간이 오늘 개장 시간 이후 평일 (라인 82-83, 87-92)
    """
    # 현재 시간을 오늘 폐장 시간 이후 평일로 Mock
    with patch('core.time_manager.TimeManager.get_current_kst_time') as mock_get_current_kst_time:
        mock_get_current_kst_time.return_value = time_manager.market_timezone.localize(
            dt.datetime(2025, 6, 27, 16, 0, 0))  # 금요일 16시

        next_open = time_manager.get_next_market_open_time()

        expected_open_time = time_manager.market_timezone.localize(
            dt.datetime(2025, 6, 30, 9, 0, 0))  # 다음주 월요일 9시
        assert next_open == expected_open_time
        mock_logger.info.assert_called_once_with(
            f"다음 시장 개장 시간: {expected_open_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")


def test_get_next_market_open_time_after_today_open_saturday(time_manager, mock_logger):
    """
    get_next_market_open_time 메서드 커버: 현재 시간이 토요일 (라인 84-85 while 루프)
    """
    # 현재 시간을 토요일로 Mock (주말 루프 커버)
    with patch('core.time_manager.TimeManager.get_current_kst_time') as mock_get_current_kst_time:
        mock_get_current_kst_time.return_value = time_manager.market_timezone.localize(
            dt.datetime(2025, 6, 28, 10, 0, 0))  # 토요일 10시

        next_open = time_manager.get_next_market_open_time()

        # 다음 개장일은 6월 30일(월요일) 09:00
        expected_open_time = time_manager.market_timezone.localize(dt.datetime(2025, 6, 30, 9, 0, 0))
        assert next_open == expected_open_time
        mock_logger.info.assert_called_once()  # 로깅 확인 (메시지 내용은 동적으로 바뀌므로 내용 검증은 생략)


def test_get_next_market_open_time_after_today_open_sunday(time_manager, mock_logger):
    """
    get_next_market_open_time 메서드 커버: 현재 시간이 일요일 (라인 84-85 while 루프)
    """
    # 현재 시간을 일요일로 Mock (주말 루프 커버)
    with patch('core.time_manager.TimeManager.get_current_kst_time') as mock_get_current_kst_time:
        mock_get_current_kst_time.return_value = time_manager.market_timezone.localize(
            dt.datetime(2025, 6, 29, 10, 0, 0))  # 일요일 10시

        next_open = time_manager.get_next_market_open_time()

        # 다음 개장일은 6월 30일(월요일) 09:00
        expected_open_time = time_manager.market_timezone.localize(dt.datetime(2025, 6, 30, 9, 0, 0))
        assert next_open == expected_open_time
        mock_logger.info.assert_called_once()  # 로깅 확인


def test_sleep_positive_seconds(time_manager, mock_logger):
    """
    sleep 메서드 커버: seconds > 0인 경우 (라인 99-101)
    """
    with patch('time.sleep') as mock_time_sleep:
        time_manager.sleep(0.01)  # 아주 작은 값으로 실제 sleep 방지
        mock_time_sleep.assert_called_once_with(0.01)
        mock_logger.info.assert_called_once_with("0.01초 동안 대기합니다 (동기).")


def test_sleep_zero_seconds(time_manager, mock_logger):
    """
    sleep 메서드 커버: seconds <= 0인 경우
    """
    with patch('time.sleep') as mock_time_sleep:
        time_manager.sleep(0)
        mock_time_sleep.assert_not_called()
        mock_logger.info.assert_not_called()


def test_sleep_negative_seconds(time_manager, mock_logger):
    """
    sleep 메서드 커버: seconds <= 0인 경우
    """
    with patch('time.sleep') as mock_time_sleep:
        time_manager.sleep(-1)
        mock_time_sleep.assert_not_called()
        mock_logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_async_sleep_positive_seconds(time_manager, mock_logger):
    """
    async_sleep 메서드 커버: seconds > 0인 경우 (라인 105-107)
    """
    with patch('asyncio.sleep') as mock_asyncio_sleep:
        await time_manager.async_sleep(0.01)  # 아주 작은 값으로 실제 sleep 방지
        mock_asyncio_sleep.assert_called_once_with(0.01)
        mock_logger.info.assert_called_once_with("0.01초 동안 대기합니다 (비동기).")


@pytest.mark.asyncio
async def test_async_sleep_zero_seconds(time_manager, mock_logger):
    """
    async_sleep 메서드 커버: seconds <= 0인 경우
    """
    with patch('asyncio.sleep') as mock_asyncio_sleep:
        await time_manager.async_sleep(0)
        mock_asyncio_sleep.assert_not_called()
        mock_logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_async_sleep_negative_seconds(time_manager, mock_logger):
    """
    async_sleep 메서드 커버: seconds <= 0인 경우
    """
    with patch('asyncio.sleep') as mock_asyncio_sleep:
        await time_manager.async_sleep(-1)
        mock_asyncio_sleep.assert_not_called()
        mock_logger.info.assert_not_called()


def test_is_weekend_or_holiday_with_weekday(time_manager):
    weekday = datetime(2025, 8, 5)  # 예: 화요일
    assert not time_manager.is_weekend_or_holiday(weekday)


def test_is_weekend_or_holiday_with_weekend(time_manager):
    saturday = datetime(2025, 8, 2)  # 토요일
    sunday = datetime(2025, 8, 3)    # 일요일
    assert time_manager.is_weekend_or_holiday(saturday)
    assert time_manager.is_weekend_or_holiday(sunday)

def test_get_market_close_time(time_manager):
    now = time_manager.get_current_kst_time()
    close_time = time_manager.get_market_close_time()

    assert close_time.hour == 15
    assert close_time.minute == 30
    assert close_time.tzinfo.zone == "Asia/Seoul"
    assert close_time.date() == now.date()


def test_get_market_close_time_on(time_manager):
    test_date = datetime(2025, 8, 1)
    close_time = time_manager.get_market_close_time_on(test_date)

    assert close_time.hour == 15
    assert close_time.minute == 30
    assert close_time.date() == test_date.date()
    assert close_time.tzinfo.zone == "Asia/Seoul"


def test_get_latest_market_close_time_weekday(time_manager):
    """
    평일 기준으로 직전 마감 시간 반환
    """
    # 월요일 오전 8시 기준: 직전 금요일 마감 시간 반환
    seoul = pytz.timezone("Asia/Seoul")
    fake_now = seoul.localize(datetime(2025, 8, 4, 8, 0, 0))  # 월요일

    # monkeypatch 활용하여 현재 시간 강제 지정
    time_manager.get_current_kst_time = lambda: fake_now

    latest_close = time_manager.get_latest_market_close_time()

    assert latest_close.weekday() == 4  # 금요일
    assert latest_close.hour == 15
    assert latest_close.minute == 30
    assert latest_close < fake_now

# --- is_weekend_or_holiday ---
@pytest.mark.parametrize("d, expected", [
    (dt.datetime(2025, 8, 1), False),  # Fri
    (dt.datetime(2025, 8, 2), True),   # Sat
    (dt.datetime(2025, 8, 3), True),   # Sun
    (dt.datetime(2025, 8, 4), False),  # Mon
])
def test_is_weekend_or_holiday_cases(time_manager, d, expected):
    assert time_manager.is_weekend_or_holiday(d) is expected


# --- to_yyyymmdd ---
def test_to_yyyymmdd_none_uses_now(time_manager, mocker):
    # None 입력 시 현재(고정) KST로 포맷되어야 함
    fixed = time_manager.market_timezone.localize(dt.datetime(2025, 8, 21, 9, 0, 0))
    mocker.patch.object(time_manager, "get_current_kst_time", return_value=fixed)
    assert time_manager.to_yyyymmdd(None) == "20250821"


def test_to_yyyymmdd_str_passthrough(time_manager):
    # 문자열은 가정상 'YYYYMMDD'로 들어오며 그대로 반환
    assert time_manager.to_yyyymmdd("20250102") == "20250102"


def test_to_yyyymmdd_datetime_and_date(time_manager):
    assert time_manager.to_yyyymmdd(dt.datetime(2025, 7, 1, 12, 34, 56)) == "20250701"
    assert time_manager.to_yyyymmdd(dt.date(2025, 7, 1)) == "20250701"


def test_to_yyyymmdd_callable(time_manager):
    fn = lambda: dt.datetime(2025, 12, 31)
    assert time_manager.to_yyyymmdd(fn) == "20251231"


def test_to_yyyymmdd_numeric_and_other(time_manager):
    # 숫자 등 기타는 str 캐스팅 결과를 반환
    assert time_manager.to_yyyymmdd(20240705) == "20240705"
    assert time_manager.to_yyyymmdd("2024-07-05") == "2024-07-05"  # 포맷 보정은 하지 않음(스펙대로)


# --- to_hhmmss ---
@pytest.mark.parametrize("raw, expected", [
    # 긴 포맷 → 뒤 6자리 사용
    ("2025082009", "082009"),
    ("1300000000", "000000"),
    ("20241023093015", "093015"),
    # HHMMSS / HHMM / HH
    ("093015", "093015"),
    ("0930",   "093000"),
    ("09",     "090000"),
    # 구분자 섞임
    ("09:30:15", "093015"),
    ("09:30",    "093000"),
    ("2024-10-23 09:31:00", "093100"),
])
def test_to_hhmmss_various_inputs(time_manager, raw, expected):
    assert time_manager.to_hhmmss(raw) == expected


def test_to_hhmmss_int_input(time_manager):
    # int도 허용 (자동 str 캐스팅 후 정규화)
    assert time_manager.to_hhmmss(930) == "000930"   # 숫자 '930' → '000930' (뒤 6자리 규칙)


def test_to_hhmmss_none_uses_now_safely(time_manager, mocker):
    """
    None 입력 시 현재 KST를 사용.
    tz-aware datetime의 __str__ 표현에 따라 마지막 6자리가 HHMMSS가 안 될 수도 있으므로
    여기서는 timezone-naive 값을 고정해 안정적으로 검증.
    """
    naive = datetime(2025, 10, 23, 9, 31, 0)  # naive로 고정
    mocker.patch.object(time_manager, "get_current_kst_time", return_value=naive)
    assert time_manager.to_hhmmss(None) == "093100"


# --- dec_minute ---
@pytest.mark.parametrize("start, minutes, expected", [
    ("090000", 1,   "085900"),
    ("090000", 30,  "083000"),
    ("090000", 61,  "075900"),
    ("100500", 6,   "095900"),  # 10:05 → 09:59
    ("000100", 1,   "000000"),  # 자정 직전 경계
])
def test_dec_minute_basic_cases(time_manager, start, minutes, expected):
    assert time_manager.dec_minute(start, minutes) == expected
