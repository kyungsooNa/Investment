# main.py (최종 버전)
from config.config_loader import load_configs


def run_web():
    """웹 서버 실행."""
    import threading
    import webbrowser
    import uvicorn
    from view.web.web_main import app

    # 설정 로드
    configs = load_configs()
    web_config = configs.get("web", {})
    host = web_config.get("host", "127.0.0.1")
    port = int(web_config.get("port", 8000))

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
    run_web()
