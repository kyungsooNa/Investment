# main.py
import os
from trading_app import TradingApp # 새로 생성한 TradingApp 클래스 임포트

# config.yaml 및 tr_ids_config.yaml 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')
TR_IDS_CONFIG_PATH = os.path.join(BASE_DIR, 'core', 'tr_ids_config.yaml')

def main():
    try:
        app = TradingApp(MAIN_CONFIG_PATH, TR_IDS_CONFIG_PATH) # TradingApp 인스턴스 생성
        app.run() # 애플리케이션 실행
    except Exception as e:
        print(f"FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: {e}")

if __name__ == "__main__":
    main()