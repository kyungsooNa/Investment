import pytest
import logging
from unittest.mock import MagicMock, AsyncMock, patch # patch, AsyncMock 추가

# 실제 strategies.GapUpPullback_strategy 경로에 맞게 수정
from strategies.GapUpPullback_strategy import GapUpPullbackStrategy

# MockBroker 클래스는 더 이상 필요 없습니다.
# 직접 AsyncMock을 사용하여 broker를 모킹할 것입니다.

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

# 기존 test_gapup_pullback_strategy_selection 함수 수정 (broker 모킹 방식 변경)
@pytest.mark.asyncio
async def test_gapup_pullback_strategy_selection():
    logger = get_test_logger()
    logger.setLevel(logging.INFO)

    # 📌 MockBroker 인스턴스 대신 AsyncMock을 사용하고 메서드를 설정합니다.
    mock_broker = AsyncMock() # AsyncMock으로 브로커 객체 생성

    # get_price_summary 메서드의 return_value 설정
    mock_broker.get_price_summary.side_effect = [
        # 첫 번째 호출 (123456)
        {
            "prev_close": 10000,
            "open": 10550,
            "low": 10200,
            "current": 10450
        },
        # 두 번째 호출 (654321)
        {
            "prev_close": 10000,
            "open": 10400,
            "low": 10350,
            "current": 10360
        }
    ]

    # get_name_by_code 메서드의 return_value 설정
    mock_broker.get_name_by_code.side_effect = ["후보종목", "제외종목"]


    strategy = GapUpPullbackStrategy(
        broker=mock_broker, # 수정된 mock_broker 주입
        min_gap_rate=5.0,
        max_pullback_rate=2.0,
        rebound_rate=2.0,
        logger=logger
    )

    result = await strategy.run(["123456", "654321"])

    assert len(result["gapup_pullback_selected"]) == 1
    assert result["gapup_pullback_selected"][0]["code"] == "123456"
    assert len(result["gapup_pullback_rejected"]) == 1
    assert result["gapup_pullback_rejected"][0]["code"] == "654321"

    # Mock 호출 검증 추가
    mock_broker.get_price_summary.assert_any_call("123456")
    mock_broker.get_price_summary.assert_any_call("654321")
    assert mock_broker.get_price_summary.call_count == 2

    mock_broker.get_name_by_code.assert_any_call("123456")
    mock_broker.get_name_by_code.assert_any_call("654321")
    assert mock_broker.get_name_by_code.call_count == 2


# --- 새로운 테스트 케이스: 필수 가격 정보 누락 시 continue 블록 커버 (수정) ---
@pytest.mark.asyncio
async def test_run_missing_price_data():
    """
    TC: GapUpPullbackStrategy.run 메서드 실행 시,
        필수 가격 정보(previous_close, open_price, low, current) 중 하나라도 누락되었을 때
        경고 로깅 후 해당 종목을 건너뛰는지 테스트합니다.
    이는 strategies/GapUpPullback_strategy.py의 35-37번 라인을 커버합니다.
    """
    logger = get_test_logger()
    logger.setLevel(logging.WARNING) # 경고 레벨 로깅을 확인하기 위해 WARNING으로 설정

    # 📌 MockBroker 인스턴스 대신 AsyncMock을 사용하고 메서드를 설정합니다.
    mock_broker = AsyncMock() # AsyncMock으로 브로커 객체 생성

    # get_price_summary가 일부 가격 정보가 누락된 응답을 반환하도록 설정
    mock_broker.get_price_summary.side_effect = [
        # 첫 번째 종목 (current 누락)
        {
            "prev_close": 10000,
            "open": 10500,
            "low": 10200,
            "current": None # 📌 누락 시뮬레이션
        },
        # 두 번째 종목 (open 누락)
        {
            "prev_close": 10000,
            # "open": 10400, # open 키가 아예 없도록 Mocking (get('open') 시 None 반환)
            "low": 10350,
            "current": 10360
        }
    ]

    # get_name_by_code 메서드의 return_value 설정
    mock_broker.get_name_by_code.side_effect = ["누락종목1", "누락종목2"]


    strategy = GapUpPullbackStrategy(
        broker=mock_broker, # 수정된 mock_broker 주입
        min_gap_rate=5.0,
        max_pullback_rate=2.0,
        rebound_rate=2.0,
        logger=logger
    )

    # logger.warning 호출을 확인하기 위해 patch
    with patch.object(logger, 'warning') as mock_logger_warning:
        # run 메서드 실행
        # 두 종목 모두 필수 가격 정보가 누락되어 선택/제외되지 않고 건너뛰어질 것임
        result = await strategy.run(["777777", "888888"])

        # 검증
        # 1. logger.warning가 각 누락 종목에 대해 호출되었는지 확인
        mock_logger_warning.assert_any_call("[데이터 누락] 누락종목1(777777) - 필수 가격 정보 없음")
        mock_logger_warning.assert_any_call("[데이터 누락] 누락종목2(888888) - 필수 가격 정보 없음")
        assert mock_logger_warning.call_count == 2 # 두 번 호출되었는지 확인

        # 2. 결과 리스트가 모두 비어 있는지 확인 (continue로 인해 추가되지 않으므로)
        assert len(result["gapup_pullback_selected"]) == 0
        assert len(result["gapup_pullback_rejected"]) == 0

        # 3. broker.get_price_summary가 각 종목에 대해 호출되었는지 확인
        mock_broker.get_price_summary.assert_any_call("777777")
        mock_broker.get_price_summary.assert_any_call("888888")
        assert mock_broker.get_price_summary.call_count == 2 # 두 번 호출되었는지 확인

        # 4. broker.get_name_by_code도 각 종목에 대해 호출되었는지 확인
        mock_broker.get_name_by_code.assert_any_call("777777")
        mock_broker.get_name_by_code.assert_any_call("888888")
        assert mock_broker.get_name_by_code.call_count == 2 # 두 번 호출되었는지 확인