# main.py (최종 버전)
import asyncio # 비동기 처리를 위해 추가
import sys # sys.exit를 위해 추가
import traceback # 예외 추적을 위해 추가

from app.trading_app import TradingApp # TradingApp 임포트


# main 함수를 비동기 함수로 선언
async def main():
    try:
        app = TradingApp()
        await app.run_async() # <--- run_async 메서드 호출 (비동기)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt 발생! 애플리케이션을 종료합니다.")
        sys.exit(0)
    except Exception as e:
        print(f"FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: {e}")
        print(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    # 비동기 메인 함수 실행
    asyncio.run(main())