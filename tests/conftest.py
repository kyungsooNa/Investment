import os
import shutil
import tempfile

import pytest

# [로그 격리] "performance"/"strategy.*" 등 core/logger.py 로거는 프로세스 전역
# 싱글톤(logging.getLogger)이며, 핸들러가 한 번 붙으면 이후 호출은 그대로 재사용한다
# (get_performance_logger 등의 `if logger.handlers: return logger` 캐시).
# 테스트별 INVESTMENT_LOG_DIR monkeypatch(아래 isolate_live_strategy_files, 함수 스코프)는
# "그 싱글톤이 최초로 핸들러를 붙이는 시점"에만 유효한데, pytest-xdist 워커는 같은 프로세스에서
# 수백 개 테스트를 순차 실행하므로 그 최초 시점이 우연히 실제 프로젝트 logs/ 로 잡히면
# 워커 수명 내내 실제 logs/performance/ 등에 테스트 데이터가 섞여 들어간다.
# → 세션(워커 프로세스) 시작 시점(pytest_configure, 어떤 테스트 모듈 임포트보다도 먼저 실행)에
#   INVESTMENT_LOG_DIR을 세션 전용 임시 디렉터리로 선고정해 이 최초 바인딩 자체를 실제
#   프로젝트 경로에서 원천 차단한다.
_SESSION_LOG_DIR: str | None = None


def pytest_configure(config):
    global _SESSION_LOG_DIR
    _SESSION_LOG_DIR = tempfile.mkdtemp(prefix="investment_test_logs_")
    os.environ["INVESTMENT_LOG_DIR"] = _SESSION_LOG_DIR
    # 브로커 계층 진단 타이머(S2/S3/S4, RQBudget/RQRetryDelay)는 실운영 진단용이라
    # 테스트에서는 기본 비활성화. 타이머 동작 자체를 검증하는 테스트는 이미
    # performance_profiler=mock_pm 을 직접 주입해 이 env 설정과 무관하게 동작한다.
    os.environ.setdefault("KIS_LAYER_TIMING", "0")


def pytest_unconfigure(config):
    if _SESSION_LOG_DIR and os.path.isdir(_SESSION_LOG_DIR):
        shutil.rmtree(_SESSION_LOG_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_live_strategy_files(monkeypatch, tmp_path):
    from strategies.first_pullback_strategy import FirstPullbackStrategy
    from strategies.high_tight_flag_strategy import HighTightFlagStrategy
    from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
    from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
    from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
    from strategies.traditional_volume_breakout_strategy import TraditionalVolumeBreakoutStrategy

    monkeypatch.setenv("INVESTMENT_LOG_DIR", str(tmp_path / "logs"))

    state_dir = tmp_path / "strategy_state"
    state_dir.mkdir()
    strategy_state_files = {
        FirstPullbackStrategy: "fp_position_state.json",
        HighTightFlagStrategy: "htf_position_state.json",
        OneilPocketPivotStrategy: "pp_position_state.json",
        OneilSqueezeBreakoutStrategy: "osb_position_state.json",
        RSI2PullbackStrategy: "rsi2_position_state.json",
        TraditionalVolumeBreakoutStrategy: "tvb_position_state.json",
    }

    for strategy_cls, file_name in strategy_state_files.items():
        monkeypatch.setattr(strategy_cls, "STATE_FILE", str(state_dir / file_name))
