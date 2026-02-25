# main.py (최종 버전)
import asyncio
import sys
import traceback

from app.trading_app import TradingApp
from config.config_loader import load_configs


def select_view_mode():
    """뷰 모드 선택: 콘솔(CLI) 또는 웹."""
    print("\n=== Investment Application ===")
    print("1. 콘솔 (CLI)")
    print("2. 웹 (브라우저)")
    while True:
        choice = input("실행 모드를 선택하세요 (1/2): ").strip()
        if choice in ('1', '2'):
            return choice
        print("잘못된 입력입니다. 1 또는 2를 입력하세요.")


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
    port = web_config.get("port", 8001)

    def open_browser():
        """서버 시작 후 브라우저 자동 오픈."""
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://{host}:{port}")

    print(f"\n[Web] http://{host}:{port} 에서 접속 가능")
    print("[Web] 환경 전환은 웹 UI 상단 배지를 클릭하세요.")
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host=host, port=port)


async def run_cli():
    """콘솔 앱 실행."""
    app = TradingApp()
    await app.run_async()


async def run_cli_main():
    try:
        await run_cli()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt 발생! 애플리케이션을 종료합니다.")
        sys.exit(0)
    except Exception as e:
        print(f"FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: {e}")
        print(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    # python main.py --web  → 웹 바로 실행
    # python main.py --cli  → 콘솔 바로 실행
    # python main.py        → 선택 메뉴
    if "--web" in sys.argv:
        mode = '2'
    elif "--cli" in sys.argv:
        mode = '1'
    else:
        mode = select_view_mode()

    if mode == '2':
        run_web()
    else:
        asyncio.run(run_cli_main())
