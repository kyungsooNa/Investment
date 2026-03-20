# tests/integration_test/test_strategies_logging_integration.py
import pytest
import importlib
import logging
import os
from unittest.mock import MagicMock, patch
from core.logger import get_strategy_logger as real_get_strategy_logger

# 테스트할 전략 목록 (모듈 경로, 클래스명, 예상되는 로그 서브 디렉토리)
STRATEGIES_TO_TEST = [
    ("strategies.oneil_pocket_pivot_strategy", "OneilPocketPivotStrategy", None),
    ("strategies.oneil_squeeze_breakout_strategy", "OneilSqueezeBreakoutStrategy", None),
    ("strategies.traditional_volume_breakout_strategy", "TraditionalVolumeBreakoutStrategy", None),
    ("strategies.program_buy_follow_strategy", "ProgramBuyFollowStrategy", None),
    ("strategies.volume_breakout_live_strategy", "VolumeBreakoutLiveStrategy", None),
    ("strategies.GapUpPullback_strategy", "GapUpPullbackStrategy", None),
]

@pytest.fixture(autouse=True)
def cleanup_logging():
    """테스트 전/후 로거 핸들러를 정리하여 파일 잠금을 해제하고 상태를 초기화합니다."""
    def clean():
        for name in list(logging.root.manager.loggerDict.keys()):
            if name.startswith("strategy."):
                logger = logging.getLogger(name)
                for h in logger.handlers[:]:
                    h.close()
                    logger.removeHandler(h)
    clean()
    yield
    clean()

@pytest.mark.parametrize("module_path, class_name, expected_subdir", STRATEGIES_TO_TEST)
def test_strategy_creates_log_file_integration(tmp_path, module_path, class_name, expected_subdir):
    """
    통합 테스트: 각 전략이 초기화될 때 지정된 디렉토리에 로그 파일을 실제로 생성하는지 검증합니다.
    실제 logs 폴더 대신 tmp_path를 사용하여 테스트 격리를 보장합니다.
    """
    # 1. 의존성 Mock 생성 (로깅 외의 동작은 차단)
    mock_ts = MagicMock()
    mock_universe = MagicMock()
    mock_tm = MagicMock()
    mock_sqs = MagicMock()
    mock_mapper = MagicMock()
    mock_broker = MagicMock()

    # 2. get_strategy_logger를 래핑하여 log_dir을 임시 경로로 리다이렉트
    def get_logger_redirected(name, log_dir="logs", sub_dir=None):
        # 실제 로거 생성 로직을 타되, log_dir만 테스트용 임시 경로로 변경
        return real_get_strategy_logger(name, log_dir=str(tmp_path), sub_dir=sub_dir)

    # 3. 해당 전략 모듈의 get_strategy_logger를 패치
    with patch(f"{module_path}.get_strategy_logger", side_effect=get_logger_redirected):
        # 모듈 동적 로드 및 클래스 가져오기
        module = importlib.import_module(module_path)
        StrategyClass = getattr(module, class_name)

        # 전략별 생성자 인자 구성
        kwargs = {}
        if class_name == "OneilSqueezeBreakoutStrategy":
            kwargs = {'stock_query_service': mock_sqs, 'universe_service': mock_universe, 'time_manager': mock_tm}
        elif class_name == "OneilPocketPivotStrategy":
            kwargs = {'stock_query_service': mock_sqs, 'universe_service': mock_universe, 'time_manager': mock_tm}
        elif "Traditional" in class_name:
            kwargs = {'stock_query_service': mock_sqs, 'stock_code_repository': mock_mapper, 'time_manager': mock_tm}
        elif "VolumeBreakoutLive" in class_name:
            kwargs = {'stock_query_service': mock_sqs, 'time_manager': mock_tm}
        elif "Program" in class_name:
            kwargs = {'stock_query_service': mock_sqs, 'time_manager': mock_tm}
        elif "GapUp" in class_name:
            kwargs = {'broker': mock_broker}

        # 전략 인스턴스화 (이 시점에 로거가 생성되고 파일이 만들어져야 함)
        strategy = StrategyClass(**kwargs)
        
        # 로그 파일에 내용이 기록되는지 확인하기 위해 테스트 로그 남기기
        test_message = f"Integration test log for {class_name}"
        strategy_logger = getattr(strategy, '_logger', getattr(strategy, 'logger', None))
        
        if strategy_logger:
            strategy_logger.setLevel(logging.INFO)
            # JsonFormatter가 문자열을 무시할 가능성을 대비해 dict 형태도 함께 남깁니다.
            strategy_logger.info({"message": test_message})
            strategy_logger.info(test_message)
            # 버퍼에 남아있는 로그를 디스크(파일)에 즉시 기록하도록 핸들러를 플러시하고 닫습니다.
            for handler in strategy_logger.handlers[:]:
                handler.flush()
                handler.close()

    # 4. 로그 파일 생성 확인
    # 예상 경로: tmp_path/strategies/{subdir}/...
    strategies_dir = tmp_path / "strategies"
    if expected_subdir:
        target_dir = strategies_dir / expected_subdir
    else:
        target_dir = strategies_dir
    
    assert target_dir.exists(), f"로그 디렉토리가 생성되지 않았습니다: {target_dir}"
    
    # .log.json 파일이 생성되었는지 확인
    log_files = list(target_dir.glob("*.log.json"))
    assert len(log_files) > 0, f"{target_dir} 경로에 로그 파일이 생성되지 않았습니다."
    
    # 5. 로그 내용 확인
    # 생성된 파일 중 하나를 읽어서 테스트 메시지가 있는지 확인
    with open(log_files[0], 'r', encoding='utf-8') as f:
        content = f.read()
        assert test_message in content, f"로그 파일에 예상된 메시지가 없습니다. 파일 내용: {content}"