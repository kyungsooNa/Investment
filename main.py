# main.py
import faulthandler
import os
from datetime import datetime
from core.logger import Logger, get_log_timestamp  # 타임스탬프 함수 임포트
from config.config_loader import load_configs

def enable_crash_dump(log_dir="logs/common"):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    timestamp = get_log_timestamp()
    dump_filename = os.path.join(log_dir, f"{timestamp}_crash_dump.log")
    
    # buffering=0 (즉시 기록)을 위해 'ab' (append binary) 모드로 오픈
    # 이 설정이 있어야 크래시 직후 메모리에 남은 데이터 없이 파일로 들어갑니다.
    f = open(dump_filename, "ab", buffering=0)
    
    # faulthandler 활성화
    faulthandler.enable(file=f)
    return f


def run_web():
    """웹 서버 실행."""
    import threading
    import webbrowser
    import uvicorn
    from view.web.web_main import app

    # 설정 로드
    configs = load_configs()
    host = configs.web.host
    port = configs.web.port

    def open_browser():
        """서버 시작 후 브라우저 자동 오픈."""
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://{host}:{port}")

    print(f"\n[Web] http://{host}:{port} 에서 접속 가능")
    print("[Web] 환경 전환은 웹 UI 상단 배지를 클릭하세요.")
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    # 1. 크래시 덤프 설정 (최상단)
    dump_file = enable_crash_dump()
    
    # 2. 로거 및 앱 실행
    logger = Logger()
    try:
        run_web()
    except Exception as e:
        logger.exception("애플리케이션 예기치 못한 종료")
    finally:
        dump_file.close()
