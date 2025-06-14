import yaml
import os

from utils import KoreaInvestEnv, KoreaInvestAPI


def main():
    config_file_path = "./config.yaml"
    if not os.path.exists(config_file_path):
        print(f"ERROR: 설정 파일이 존재하지 않습니다: {config_file_path}")
        print("config.yaml 파일을 스크립트와 같은 디렉토리에 두거나 경로를 수정해주세요.")
        return

    try:
        with open(config_file_path, encoding='UTF-8') as f:
            cfg_data = yaml.load(f, Loader=yaml.FullLoader) # cfg_data로 변수명 변경
    except Exception as e:
        print(f"ERROR: config.yaml 파일을 로드하는 중 오류 발생: {e}")
        return

    # 1. KoreaInvestEnv 객체 생성
    env_cls = KoreaInvestEnv(cfg_data) # 초기 설정 데이터를 전달

    # 2. API 접근 토큰 발급 (가장 중요!)
    # 이 시점에서 env_cls 내부에 access_token이 업데이트됩니다.
    access_token_result = env_cls.get_access_token()
    if not access_token_result: # 토큰 발급 실패 시
        print("ERROR: API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")
        return

    # 3. KoreaInvestAPI 객체 생성을 위한 최신 설정 및 헤더 가져오기
    # env_cls 내부에 업데이트된 access_token이 포함된 최신 full_config와 base_headers를 가져옵니다.
    full_config_with_token = env_cls.get_full_config()
    base_headers_with_token = env_cls.get_base_headers()

    # 4. KoreaInvestAPI 객체 생성 (업데이트된 설정과 헤더 전달)
    korea_invest_api = KoreaInvestAPI(full_config_with_token, base_headers=base_headers_with_token)

    print(f"\n성공적으로 API 클라이언트를 초기화했습니다: {korea_invest_api}")

    # --- 이제 초기화된 korea_invest_api 객체를 사용하여 실제 API를 호출할 수 있습니다. ---
    # 예시: 주식 현재가 조회 (한국투자증권 Open API 문서에 맞는 종목 코드를 사용하세요)
    stock_price = korea_invest_api.get_current_price("005930") # 삼성전자 예시
    if stock_price:
        print(f"\n삼성전자 현재가: {stock_price}")
    else:
        print("\n현재가 조회 실패.")

    # 예시: 계좌 잔고 조회
    account_balance = korea_invest_api.get_account_balance()
    if account_balance:
        print(f"\n계좌 잔고: {account_balance}")
    else:
        print("\n계좌 잔고 조회 실패.")


if __name__ == "__main__":
    main()