import os
import subprocess
import sys
import time
import pytest
from core.logger import get_log_timestamp

def test_faulthandler_records_crash(tmp_path):
    """
    고의로 Segmentation Fault를 발생시키는 별도 프로세스를 실행하고
    faulthandler가 지정된 파일에 덤프를 남기는지 검증합니다.
    """
    # 1. 테스트용 임시 로그 디렉토리 설정
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    
    # 2. 실행할 테스트 스크립트 생성
    # 실제 main.py의 로직을 모방하여 크래시를 유발합니다.
    test_script = f"""
import faulthandler
import os
import ctypes
from core.logger import get_log_timestamp

def run_test():
    log_file_path = os.path.join(r"{log_dir}", "test_crash.log")
    with open(log_file_path, "ab", buffering=0) as f:
        faulthandler.enable(file=f)
        # 즉사 유도
        ctypes.string_at(0)

if __name__ == "__main__":
    run_test()
"""
    script_path = tmp_path / "crash_app.py"
    script_path.write_text(test_script, encoding="utf-8")

    # 3. 서브프로세스로 실행
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd() # 현재 프로젝트 루트를 경로에 추가
    
    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env
    )
    process.wait() # 프로세스가 죽을 때까지 대기

    # 4. 검증: 파일이 생성되었고 내용에 'access violation' 또는 'string_at'이 있는지 확인
    log_file = log_dir / "test_crash.log"
    assert log_file.exists(), "크래시 덤프 파일이 생성되지 않았습니다."
    
    content = log_file.read_text(encoding="ansi") # Windows 환경은 보통 ansi/cp949로 기록될 수 있음
    assert "string_at" in content or "access violation" in content.lower(), "덤프 내용이 올바르지 않습니다."