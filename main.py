# main.py
import os
from trading_app import TradingApp # 새로 생성한 TradingApp 클래스 임포트

# config.yaml 및 tr_ids_config.yaml 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')
TR_IDS_CONFIG_PATH = os.path.join(BASE_DIR, 'core', 'tr_ids_config.yaml')

def main():
    try:
        # TradingApp 인스턴스 생성 및 실행
        # 초기화 과정에서 오류가 발생하면 main 함수에서 처리 (예: 프로그램 종료)
        app = TradingApp(MAIN_CONFIG_PATH, TR_IDS_CONFIG_PATH)
        app.run() # 애플리케이션 메인 루프 실행
    except Exception as e:
        # TradingApp 초기화 또는 실행 중 발생한 예외를 여기서 최종 처리
        print(f"FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: {e}")

if __name__ == "__main__":
    main()
