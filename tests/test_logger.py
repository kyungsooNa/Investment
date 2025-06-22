import os
import glob
from core.logger import Logger

def test_logger_creates_log_files(tmp_path):
    # Given
    log_dir = tmp_path / "logs"
    logger = Logger(log_dir=str(log_dir))

    # When
    logger.info("info message")
    logger.debug("debug message")
    logger.warning("warning message")
    logger.error("error message")
    logger.critical("critical message")

    # Then
    log_files = list(log_dir.glob("*.log"))
    assert len(log_files) == 2  # operational, debug

    # 파일 이름 형식 확인
    assert any("debug" in f.name for f in log_files)
    assert any("operational" in f.name for f in log_files)

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert "info message" in content
        assert "warning message" in content
        assert "error message" in content
        assert "critical message" in content
        if "debug" in f.name:
            assert "debug message" in content
        else:
            assert "debug message" not in content
