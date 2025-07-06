import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from io import StringIO
import builtins

# 테스트 대상 모듈 임포트
from app.cli_view import CLIView
from core.logger import Logger
from core.time_manager import TimeManager


# --- Pytest 픽스처 정의 ---

@pytest.fixture
def mock_time_manager():
    """TimeManager의 MagicMock 인스턴스를 제공하는 픽스처."""
    mock = MagicMock(spec=TimeManager)
    mock.get_current_kst_time.return_value = MagicMock(strftime=MagicMock(return_value="2025-07-07 10:00:00"))
    mock.is_market_open.return_value = True  # 기본 시장 개장 상태
    return mock


@pytest.fixture
def mock_logger():
    """Logger의 MagicMock 인스턴스를 제공하는 픽스처."""
    mock = MagicMock(spec=Logger)
    mock.info = MagicMock()
    mock.warning = MagicMock()
    mock.error = MagicMock()
    mock.critical = MagicMock()
    mock.debug = MagicMock()
    return mock


@pytest.fixture
def cli_view_instance(mock_time_manager, mock_logger):
    """CLIView 인스턴스를 제공하는 픽스처."""
    return CLIView(time_manager=mock_time_manager, logger=mock_logger)


# --- CLIView 메서드 테스트 케이스 ---

@pytest.mark.asyncio
async def test_display_welcome_message(cli_view_instance, capsys):
    """환영 메시지 출력을 테스트합니다."""
    cli_view_instance.display_welcome_message()
    captured = capsys.readouterr()
    assert "파이썬 증권 자동매매 시스템" in captured.out


@pytest.mark.asyncio
async def test_get_user_input(cli_view_instance):
    """사용자 입력 받기 기능을 테스트합니다."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = "test_input_value"

        prompt = "Enter something: "
        result = await cli_view_instance.get_user_input(prompt)

        mock_to_thread.assert_awaited_once_with(builtins.input, prompt)
        assert result == "test_input_value"


def test_display_current_time(cli_view_instance, capsys, mock_time_manager):
    """현재 시각 출력을 테스트합니다."""
    mock_time_manager.get_current_kst_time.return_value.strftime.return_value = "2025-07-07 10:30:00"
    cli_view_instance.display_current_time()
    captured = capsys.readouterr()
    assert "현재 시각: 2025-07-07 10:30:00" in captured.out


def test_display_market_status_open(cli_view_instance, capsys):
    """시장 개장 상태 출력을 테스트합니다."""
    cli_view_instance.display_market_status(True)
    captured = capsys.readouterr()
    assert "시장 상태: 개장" in captured.out


def test_display_market_status_closed(cli_view_instance, capsys):
    """시장 폐장 상태 출력을 테스트합니다."""
    cli_view_instance.display_market_status(False)
    captured = capsys.readouterr()
    assert "시장 상태: 폐장" in captured.out


def test_display_account_balance(cli_view_instance, capsys):
    """계좌 잔고 정보 출력을 테스트합니다."""
    balance_info = {
        'dnca_tot_amt': '1000000',
        'tot_evlu_amt': '1200000',
        'tot_evlu_pfls_amt': '200000',
        'tot_evlu_pfls_rt': '20.00'
    }
    cli_view_instance.display_account_balance(balance_info)
    captured = capsys.readouterr()
    assert "예수금: 1000000원" in captured.out
    assert "총 평가 금액: 1200000원" in captured.out
    assert "총 평가 손익: 200000원" in captured.out
    assert "총 손익률: 20.00%" in captured.out


def test_display_stock_info_found(cli_view_instance, capsys):
    """종목 정보(발견) 출력을 테스트합니다."""
    stock_summary = {
        'name': '삼성전자',
        'current': '70000',
        'diff': '500',
        'diff_rate': '0.72',
        'volume': '1000000'
    }
    cli_view_instance.display_stock_info(stock_summary)
    captured = capsys.readouterr()
    assert "종목명: 삼성전자" in captured.out
    assert "현재가: 70000원" in captured.out
    assert "전일 대비: 500원 (0.72%)" in captured.out
    assert "거래량: 1000000" in captured.out


def test_display_stock_info_not_found(cli_view_instance, capsys):
    """종목 정보(찾을 수 없음) 출력을 테스트합니다."""
    cli_view_instance.display_stock_info(None)
    captured = capsys.readouterr()
    assert "종목 정보를 찾을 수 없습니다." in captured.out


def test_display_transaction_result_success(cli_view_instance, capsys):
    """매수/매도 거래 결과 성공 출력을 테스트합니다."""
    result_info = {'rt_cd': '0', 'ord_no': '12345', 'ord_tmd': '10:00:00'}
    cli_view_instance.display_transaction_result(result_info, "매수")
    captured = capsys.readouterr()
    assert "✔️ 매수 성공!" in captured.out
    assert "주문 번호: 12345" in captured.out


def test_display_transaction_result_failure(cli_view_instance, capsys):
    """매수/매도 거래 결과 실패 출력을 테스트합니다."""
    result_info = {'rt_cd': '1', 'msg1': '잔고 부족'}
    cli_view_instance.display_transaction_result(result_info, "매도")
    captured = capsys.readouterr()
    assert "❌ 매도 실패: 잔고 부족" in captured.out


def test_display_app_start_error(cli_view_instance, capsys):
    """애플리케이션 시작 오류 메시지 출력을 테스트합니다."""
    cli_view_instance.display_app_start_error("설정 파일 없음")
    captured = capsys.readouterr()
    assert "[오류] 애플리케이션 시작 실패: 설정 파일 없음" in captured.out
    assert "설정 파일을 확인하거나 관리자에게 문의하세요." in captured.out


def test_display_strategy_running_message(cli_view_instance, capsys):
    """전략 실행 시작 메시지 출력을 테스트합니다."""
    cli_view_instance.display_strategy_running_message("모멘텀")
    captured = capsys.readouterr()
    assert "--- 모멘텀 전략 실행 시작 ---" in captured.out


def test_display_top_stocks_failure(cli_view_instance, capsys):
    """시가총액 상위 종목 조회 실패 메시지 출력을 테스트합니다."""
    cli_view_instance.display_top_stocks_failure("API 응답 오류")
    captured = capsys.readouterr()
    assert "시가총액 상위 종목 조회 실패: API 응답 오류" in captured.out


def test_display_top_stocks_success(cli_view_instance, capsys):
    """시가총액 상위 종목 조회 성공 메시지 출력을 테스트합니다."""
    cli_view_instance.display_top_stocks_success()
    captured = capsys.readouterr()
    assert "시가총액 상위 종목 조회 완료." in captured.out


def test_display_no_stocks_for_strategy(cli_view_instance, capsys):
    """전략 실행을 위한 종목 없음 메시지 출력을 테스트합니다."""
    cli_view_instance.display_no_stocks_for_strategy()
    captured = capsys.readouterr()
    assert "전략을 실행할 종목이 없습니다." in captured.out


def test_display_strategy_results(cli_view_instance, capsys):
    """전략 실행 결과 요약 출력을 테스트합니다."""
    results = {
        'total_processed': 10,
        'buy_attempts': 5,
        'buy_successes': 3,
        'sell_attempts': 2,
        'sell_successes': 1,
        'execution_time': 15.345
    }
    cli_view_instance.display_strategy_results("모멘텀", results)
    captured = capsys.readouterr()
    assert "--- 모멘텀 전략 실행 결과 ---" in captured.out
    assert "총 처리 종목: 10개" in captured.out
    assert "매수 성공 종목: 3개" in captured.out
    assert "전략 실행 시간: 15.35초" in captured.out


def test_display_strategy_results_non_numeric_execution_time(cli_view_instance, capsys):
    """전략 실행 결과 요약 출력을 테스트합니다 (실행 시간이 숫자가 아닐 때)."""
    results = {
        'total_processed': 1,
        'buy_attempts': 1,
        'buy_successes': 1,
        'sell_attempts': 0,
        'sell_successes': 0,
        'execution_time': "N/A"  # 숫자가 아닌 값
    }
    cli_view_instance.display_strategy_results("테스트 전략", results)
    captured = capsys.readouterr()
    assert "총 처리 종목: 1개" in captured.out
    assert "전략 실행 시간: 0.00초" in captured.out  # 0.00으로 변환되어야 함


def test_display_strategy_error(cli_view_instance, capsys):
    """전략 실행 중 오류 메시지 출력을 테스트합니다."""
    cli_view_instance.display_strategy_error("데이터 부족")
    captured = capsys.readouterr()
    assert "[오류] 전략 실행 중 문제 발생: 데이터 부족" in captured.out


def test_display_invalid_menu_choice(cli_view_instance, capsys):
    """잘못된 메뉴 선택 메시지 출력을 테스트합니다."""
    cli_view_instance.display_invalid_menu_choice()
    captured = capsys.readouterr()
    assert "잘못된 메뉴 선택입니다. 다시 시도해주세요." in captured.out


def test_display_warning_strategy_market_closed(cli_view_instance, capsys):
    """시장이 닫혔을 때 전략 실행 경고 메시지 출력을 테스트합니다."""
    cli_view_instance.display_warning_strategy_market_closed()
    captured = capsys.readouterr()
    assert "⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다." in captured.out


def test_display_follow_through_stocks_found_dict(cli_view_instance, capsys):
    """Follow Through 종목 목록(dict 형식) 출력을 테스트합니다."""
    stocks = [{'name': '삼성전자', 'code': '005930'}, {'name': 'SK하이닉스', 'code': '000660'}]
    cli_view_instance.display_follow_through_stocks(stocks)
    captured = capsys.readouterr()
    assert "✔️ Follow Through 종목:" in captured.out
    assert " - 삼성전자(005930)" in captured.out
    assert " - SK하이닉스(000660)" in captured.out


def test_display_follow_through_stocks_found_string(cli_view_instance, capsys):
    """Follow Through 종목 목록(문자열 형식) 출력을 테스트합니다."""
    stocks = ['005930', '000660']  # 문자열 형식의 종목 코드
    cli_view_instance.display_follow_through_stocks(stocks)
    captured = capsys.readouterr()
    assert "✔️ Follow Through 종목:" in captured.out
    assert " - 005930" in captured.out
    assert " - 000660" in captured.out


def test_display_follow_through_stocks_not_found(cli_view_instance, capsys):
    """Follow Through 종목 목록(없음) 출력을 테스트합니다."""
    cli_view_instance.display_follow_through_stocks([])
    captured = capsys.readouterr()
    assert "✔️ Follow Through 종목:" in captured.out
    assert "   없음" in captured.out


def test_display_not_follow_through_stocks_found_dict(cli_view_instance, capsys):
    """Follow 실패 종목 목록(dict 형식) 출력을 테스트합니다."""
    stocks = [{'name': '카카오', 'code': '035720'}]
    cli_view_instance.display_not_follow_through_stocks(stocks)
    captured = capsys.readouterr()
    assert "❌ Follow 실패 종목:" in captured.out
    assert " - 카카오(035720)" in captured.out


def test_display_not_follow_through_stocks_found_string(cli_view_instance, capsys):
    """Follow 실패 종목 목록(문자열 형식) 출력을 테스트합니다."""
    stocks = ['035720', '123450']  # 문자열 형식의 종목 코드
    cli_view_instance.display_not_follow_through_stocks(stocks)
    captured = capsys.readouterr()
    assert "❌ Follow 실패 종목:" in captured.out
    assert " - 035720" in captured.out
    assert " - 123450" in captured.out


def test_display_not_follow_through_stocks_not_found(cli_view_instance, capsys):
    """Follow 실패 종목 목록(없음) 출력을 테스트합니다."""
    cli_view_instance.display_not_follow_through_stocks([])
    captured = capsys.readouterr()
    assert "❌ Follow 실패 종목:" in captured.out
    assert "   없음" in captured.out


def test_display_gapup_pullback_selected_stocks_found(cli_view_instance, capsys):
    """GapUpPullback 후보 종목 목록(발견) 출력을 테스트합니다."""
    stocks = [{'name': 'LG전자', 'code': '066570'}]
    cli_view_instance.display_gapup_pullback_selected_stocks(stocks)
    captured = capsys.readouterr()
    assert "✔️ 후보 종목:" in captured.out
    assert " - LG전자(066570)" in captured.out


def test_display_gapup_pullback_selected_stocks_not_found(cli_view_instance, capsys):
    """GapUpPullback 후보 종목 목록(없음) 출력을 테스트합니다."""
    cli_view_instance.display_gapup_pullback_selected_stocks([])
    captured = capsys.readouterr()
    assert "✔️ 후보 종목:" in captured.out
    assert "   없음" in captured.out


def test_display_gapup_pullback_rejected_stocks_found(cli_view_instance, capsys):
    """GapUpPullback 제외 종목 목록(발견) 출력을 테스트합니다."""
    stocks = [{'name': '현대차', 'code': '005380'}]
    cli_view_instance.display_gapup_pullback_rejected_stocks(stocks)
    captured = capsys.readouterr()
    assert "❌ 제외 종목:" in captured.out
    assert " - 현대차(005380)" in captured.out


def test_display_gapup_pullback_rejected_stocks_not_found(cli_view_instance, capsys):
    """GapUpPullback 제외 종목 목록(없음) 출력을 테스트합니다."""
    cli_view_instance.display_gapup_pullback_rejected_stocks([])
    captured = capsys.readouterr()
    assert "❌ 제외 종목:" in captured.out
    assert "   없음" in captured.out


def test_display_invalid_input_warning(cli_view_instance, capsys):
    """사용자 입력 경고 메시지 출력을 테스트합니다."""
    cli_view_instance.display_invalid_input_warning("유효하지 않은 숫자")
    captured = capsys.readouterr()
    assert "WARNING: 유효하지 않은 숫자" in captured.out


def test_display_exit_message(cli_view_instance, capsys):
    """종료 메시지 출력을 테스트합니다."""
    cli_view_instance.display_exit_message()
    captured = capsys.readouterr()
    assert "애플리케이션을 종료합니다." in captured.out


def test_display_token_invalidated_message(cli_view_instance, capsys):
    """토큰 무효화 메시지 출력을 테스트합니다."""
    cli_view_instance.display_token_invalidated_message()
    captured = capsys.readouterr()
    assert "토큰이 무효화되었습니다. 다음 요청 시 새 토큰이 발급됩니다." in captured.out


def test_display_account_balance_failure(cli_view_instance, capsys):
    """계좌 잔고 조회 실패 메시지 출력을 테스트합니다."""
    cli_view_instance.display_account_balance_failure()
    captured = capsys.readouterr()
    assert "계좌 잔고 조회에 실패했습니다." in captured.out


def test_display_stock_code_not_found(cli_view_instance, capsys):
    """종목 코드를 찾을 수 없을 때 메시지 출력을 테스트합니다."""
    cli_view_instance.display_stock_code_not_found("없는종목")
    captured = capsys.readouterr()
    assert "'없는종목'에 해당하는 종목 코드를 찾을 수 없습니다." in captured.out


def test_display_menu(cli_view_instance, capsys):
    """메뉴 출력 기능을 테스트합니다."""
    cli_view_instance.display_menu(
        env_type="모의투자",
        current_time_str="2025-07-07 11:00:00 KST+0900",
        market_status_str="열려있음"
    )
    captured = capsys.readouterr()
    assert "--- 한국투자증권 API 애플리케이션 (환경: 모의투자, 현재: 2025-07-07 11:00:00 KST+0900, 시장: 열려있음) ---" in captured.out
    assert "1. 주식 현재가 조회 (종목코드 입력)" in captured.out
    assert "98. 토큰 무효화" in captured.out
    assert "99. 종료" in captured.out


@pytest.mark.asyncio
async def test_select_environment_input(cli_view_instance):
    """환경 선택 입력 기능을 테스트합니다."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = "1"  # 사용자 입력 시뮬레이션

        result = await cli_view_instance.select_environment_input()

        mock_to_thread.assert_awaited_once_with(builtins.input, "환경을 선택하세요 (숫자 입력): ")
        assert result == "1"
