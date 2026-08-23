"""키움 REST 시세 API 의 endpoint 경로와 api-id 를 한 곳에 모은다.

출처: 키움 공식 저장소 `Kiwoom-Securities/kiwoom-rest-api` 의
`kiwoom/_data/kiwoom_api_spec.json`. 차트 계열(일봉/분봉)은 api-id 만 다르고
경로는 `/api/dostk/chart` 로 같다.
"""


class KiwoomApiId:
    STOCK_INFO = "ka10001"        # 주식기본정보요청
    DAILY_CHART = "ka10081"       # 주식일봉차트조회요청
    MINUTE_CHART = "ka10080"      # 주식분봉차트조회요청
    ACCOUNT_BALANCE = "kt00018"   # 계좌평가잔고내역요청
    ACCOUNT_DEPOSIT = "kt00001"   # 예수금상세현황요청
    ORDER_BUY = "kt10000"         # 주식 매수주문
    ORDER_SELL = "kt10001"        # 주식 매도주문
    ORDER_CANCEL = "kt10003"      # 주식 취소주문


_PATHS = {
    KiwoomApiId.STOCK_INFO: "/api/dostk/stkinfo",
    KiwoomApiId.DAILY_CHART: "/api/dostk/chart",
    KiwoomApiId.MINUTE_CHART: "/api/dostk/chart",
    KiwoomApiId.ACCOUNT_BALANCE: "/api/dostk/acnt",
    KiwoomApiId.ACCOUNT_DEPOSIT: "/api/dostk/acnt",
    KiwoomApiId.ORDER_BUY: "/api/dostk/ordr",
    KiwoomApiId.ORDER_SELL: "/api/dostk/ordr",
    KiwoomApiId.ORDER_CANCEL: "/api/dostk/ordr",
}


def path_for(api_id: str) -> str:
    try:
        return _PATHS[api_id]
    except KeyError:
        raise ValueError(f"경로가 등록되지 않은 키움 api-id: {api_id}")
