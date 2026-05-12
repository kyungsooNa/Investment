import pytest
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch
import core.logger

# 테스트할 전략 클래스와 예상되는 로거 이름 매핑
STRATEGIES = [
    ("strategies.oneil_pocket_pivot_strategy", "OneilPocketPivotStrategy", "OneilPocketPivot"),
    ("strategies.oneil_squeeze_breakout_strategy", "OneilSqueezeBreakoutStrategy", "OneilSqueezeBreakout"),
    ("strategies.traditional_volume_breakout_strategy", "TraditionalVolumeBreakoutStrategy", "TraditionalVolumeBreakout"),
    ("strategies.program_buy_follow_strategy", "ProgramBuyFollowStrategy", "ProgramBuyFollow"),
    ("strategies.volume_breakout_live_strategy", "VolumeBreakoutLiveStrategy", "VolumeBreakoutLive"),
    ("strategies.GapUpPullback_strategy", "GapUpPullbackStrategy", "GapUpPullback"),
]

@pytest.mark.parametrize("module_path, class_name, expected_logger_name", STRATEGIES)
def test_strategies_init_logger(module_path, class_name, expected_logger_name):
    """
    모든 전략이 초기화 시 get_strategy_logger를 사용하여 
    올바른 이름의 전용 로거를 생성하는지 검증합니다.
    """
    # 1. 모듈 및 클래스 동적 로드
    module = importlib.import_module(module_path)
    StrategyClass = getattr(module, class_name)
    
    # 2. 의존성 Mock 생성
    ts = MagicMock()
    universe = MagicMock()
    tm = MagicMock()
    sqs = MagicMock()
    mapper = MagicMock()
    broker = MagicMock()
    
    # 3. 전략별 생성자 인자 준비
    kwargs = {}
    if class_name == "OneilSqueezeBreakoutStrategy":
        kwargs = {'stock_query_service': sqs, 'universe_service': universe, 'market_clock': tm}
    elif class_name == "OneilPocketPivotStrategy":
        kwargs = {'stock_query_service': sqs, 'universe_service': universe, 'market_clock': tm}
    elif "Traditional" in class_name:
        kwargs = {'stock_query_service': sqs, 'stock_code_repository': mapper, 'market_clock': tm}
    elif "VolumeBreakoutLive" in class_name:
        kwargs = {'stock_query_service': sqs, 'market_clock': tm}
    elif "Program" in class_name:
        kwargs = {'stock_query_service': sqs, 'market_clock': tm}
    elif "GapUp" in class_name:
        kwargs = {'broker': broker}
        
    # 4. get_strategy_logger 패치 및 검증
    with patch(f"{module_path}.get_strategy_logger") as mock_get_logger:
        # 전략 인스턴스화 (logger 인자를 주지 않음 -> 내부에서 get_strategy_logger 호출 유도)
        strategy = StrategyClass(**kwargs)
        
        # get_strategy_logger가 예상된 이름으로 호출되었는지 확인
        mock_get_logger.assert_called_once()
        args, _ = mock_get_logger.call_args
        assert args[0] == expected_logger_name
        
        # 전략 인스턴스에 로거가 할당되었는지 확인
        logger_attr = getattr(strategy, '_logger', getattr(strategy, 'logger', None))
        assert logger_attr == mock_get_logger.return_value


def test_default_strategy_logger_uses_test_log_dir_env(monkeypatch, tmp_path):
    """테스트 환경에서는 기본 전략 로그가 실제 logs/strategies에 쓰이지 않는다."""
    redirected_log_dir = tmp_path / "isolated_logs"
    monkeypatch.setenv("INVESTMENT_LOG_DIR", str(redirected_log_dir))

    logger = core.logger.get_strategy_logger("EnvIsolationCheck")
    logger.info({"event": "test_log_isolation"})
    core.logger.shutdown_logging()

    assert list((redirected_log_dir / "strategies").glob("*EnvIsolationCheck*.log.json*"))
    assert not list(Path("logs/strategies").glob("*EnvIsolationCheck*.log.json*"))
