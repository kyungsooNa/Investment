# main.py
import os
from core.config_loader import load_config  # core 폴더에서 임포트
from api.env import KoreaInvestEnv  # api 폴더에서 임포트
from api.client import KoreaInvestAPI  # api 폴더에서 임포트

# config.yaml 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')


def main():
    # 1. 환경 설정 로드 및 초기화
    try:
        config_data = load_config(CONFIG_PATH)
        env = KoreaInvestEnv(config_data)
    except ValueError as e:
        print(f"ERROR: 환경 설정 초기화 실패: {e}")
        return
    except FileNotFoundError as e:
        print(f"ERROR: 설정 파일을 찾을 수 없습니다: {e}")
        return

    # 2. 접근 토큰 발급
    access_token = env.get_access_token()
    if not access_token:
        print("ERROR: API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")
        return

    # 3. API 클라이언트 초기화 (env 객체를 바로 전달)
    try:
        korea_invest_api = KoreaInvestAPI(env)
        print(f"\n성공적으로 API 클라이언트를 초기화했습니다: {korea_invest_api}")
    except ValueError as e:
        print(f"ERROR: API 클라이언트 초기화 실패: {e}")
        return

    # --- 주식 현재가 조회 ---
    stock_code_samsung = "005930"  # 삼성전자
    current_price_samsung = korea_invest_api.quotations.get_current_price(stock_code_samsung)
    if current_price_samsung and current_price_samsung.get('rt_cd') == '0':
        print(f"\n삼성전자 현재가: {current_price_samsung}")
    else:
        print(f"\n삼성전자 현재가 조회 실패.")

    # --- 계좌 잔고 조회 ---
    account_balance = korea_invest_api.account.get_account_balance()
    if account_balance and account_balance.get('rt_cd') == '0':
        print(f"\n계좌 잔고: {account_balance}")
    else:
        print(f"\n계좌 잔고 조회 실패.")

    # --- 주식 매수 주문 예시 ---
    print("\n--- 주식 매수 주문 시도 ---")
    order_stock_code = "005930"  # 주문할 종목코드 (삼성전자)
    order_price = "58500"  # 예시: 지정가 58,500원 (문자열로 전달)
    order_qty = "1"  # 예시: 1주 (문자열로 전달)
    order_dvsn = "00"  # 주문 유형: "00" 지정가

    buy_order_result = korea_invest_api.trading.place_stock_order(
        order_stock_code,
        order_price,
        order_qty,
        "매수",  # 매매 구분: "매수"
        order_dvsn  # 주문 유형
    )

    if buy_order_result and buy_order_result.get('rt_cd') == '0':
        print(f"주식 매수 주문 성공: {buy_order_result}")
    else:
        print(f"주식 매수 주문 실패: {buy_order_result}")

    # --- 주식 매도 주문 예시 ---
    # 주석을 해제하고 사용 시, 보유한 주식이 있어야 합니다.
    # print("\n--- 주식 매도 주문 시도 ---")
    # order_type = "매도"
    # order_stock_code = "005930"
    # order_price = "58000" # 예시: 지정가 58,000원
    # order_qty = "1" # 예시: 1주
    # order_dvsn = "00" # 지정가

    # sell_order_result = korea_invest_api.trading.place_stock_order(
    #     order_stock_code,
    #     order_price,
    #     order_qty,
    #     "매도", # 매매 구분
    #     order_dvsn # 주문 유형
    # )

    # if sell_order_result and sell_order_result.get('rt_cd') == '0':
    #     print(f"주식 매도 주문 성공: {sell_order_result}")
    # else:
    #     print(f"주식 매도 주문 실패: {sell_order_result}")
    # --- 시가총액 상위 종목 조회 예시 (모의투자 미지원) ---
    print("\n--- 시가총액 상위 종목 조회 시도 (모의투자 미지원) ---")
    # 이 API는 모의투자를 지원하지 않으므로, 실전 환경에서만 작동합니다.
    # config.yaml의 is_paper_trading: False로 변경 필요
    top_market_cap_stocks = korea_invest_api.quotations.get_top_market_cap_stocks(market_code="0000")  # 0000: 전체 시장

    if top_market_cap_stocks and top_market_cap_stocks.get('rt_cd') == '0':
        print(f"성공: 시가총액 상위 종목 목록:")
        for stock_info in top_market_cap_stocks.get('output', []):
            print(f"  순위: {stock_info.get('data_rank', '')}, "
                  f"종목명: {stock_info.get('hts_kor_isnm', '')}, "
                  f"시가총액: {stock_info.get('stck_avls', '')}, "
                  f"현재가: {stock_info.get('stck_prpr', '')}")
    else:
        print(f"실패: 시가총액 상위 종목 조회.")

if __name__ == "__main__":
    main()