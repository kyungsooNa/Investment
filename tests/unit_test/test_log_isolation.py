"""테스트 세션이 실제 프로젝트 logs/ 를 오염시키지 않는지 검증.

pytest-xdist 워커는 같은 프로세스에서 다수 테스트를 순차 실행하며, core/logger.py의
"performance" 등 로거는 프로세스 전역 싱글톤이라 최초 핸들러 부착 시점의 log_dir이
워커 수명 내내 고정된다. tests/conftest.py::pytest_configure 가 그 최초 바인딩을
세션 전용 임시 디렉터리로 선점해야 한다.
"""
import os


def test_investment_log_dir_isolated_from_project_logs():
    log_dir = os.environ.get("INVESTMENT_LOG_DIR")
    assert log_dir, "pytest_configure가 INVESTMENT_LOG_DIR을 세션 시작 시 설정해야 한다"

    project_logs_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    )
    resolved = os.path.abspath(log_dir)
    assert resolved != project_logs_dir
    assert not resolved.startswith(project_logs_dir + os.sep), (
        f"INVESTMENT_LOG_DIR({resolved})가 실제 프로젝트 logs/ 아래를 가리키면 안 된다"
    )


def test_layer_timing_disabled_by_default_in_tests():
    """S2/S3/S4·RQBudget/RQRetryDelay 진단 타이머는 테스트에서 기본 비활성화되어야 한다."""
    assert os.environ.get("KIS_LAYER_TIMING") == "0"
