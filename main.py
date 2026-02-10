# main.py (최종 버전)
import asyncio
import sys
import traceback

from app.trading_app import TradingApp


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
    import uvicorn
    from view.web.web_main import app
    print("\n[Web] http://localhost:8000 에서 접속 가능")
    uvicorn.run(app, host="0.0.0.0", port=8000)


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
    mode = select_view_mode()
    if mode == '2':
        run_web()
    else:
        asyncio.run(run_cli_main())
