# api/trading.py
import requests
import json
import os
import certifi  # hashkey 호출에 필요
import hashlib  # hashkey 생성에 필요

from api.base import _KoreaInvestAPIBase


class KoreaInvestTradingAPI(_KoreaInvestAPIBase):
    """
    한국투자증권 Open API의 주식 거래/주문 관련 기능을 담당하는 클래스입니다.
    """

    def _get_hashkey(self, data):
        """
        주문 요청 Body를 기반으로 Hashkey를 생성하여 반환합니다.
        이는 별도의 API 호출을 통해 이루어집니다.
        """
        body_json_str = json.dumps(data)
        hashkey_url = f"{self._base_url}/uapi/hashkey"

        # Hashkey API 호출을 위한 헤더 (기본 헤더 재활용)
        hashkey_headers = self._headers.copy()
        hashkey_headers["Content-Type"] = "application/json; charset=utf-8"

        try:
            hash_response = requests.post(hashkey_url, headers=hashkey_headers, data=body_json_str,
                                          verify=certifi.where())
            hash_response.raise_for_status()
            hash_data = hash_response.json()
            calculated_hashkey = hash_data.get('HASH')

            if not calculated_hashkey:
                print(f"ERROR: Hashkey API 응답에 HASH 값이 없습니다: {hash_data}")
                return None

            print(f"INFO: Hashkey 계산 성공: {calculated_hashkey}")
            return calculated_hashkey

        except requests.exceptions.RequestException as e:
            print(f"ERROR: Hashkey API 호출 중 네트워크 오류: {e}")
            return None
        except json.JSONDecodeError:
            print(f"ERROR: Hashkey API 응답 JSON 디코딩 실패: {hash_response.text}")
            return None
        except Exception as e:
            print(f"ERROR: Hashkey API 호출 중 알 수 없는 오류: {e}")
            return None

    def place_stock_order(self, stock_code, order_price, order_qty, trade_type, order_dvsn):
        """
        주식 매수/매도 주문을 제출하는 메서드 (모의투자용 예시).
        """
        path = "/uapi/domestic-stock/v1/trading/order-cash"  # 주문 API 경로

        if trade_type == "매수":
            tr_id = "VTTC0012U"  # 매수 TR ID
        elif trade_type == "매도":
            tr_id = "VTTC0011U"  # 매도 TR ID
        else:
            print("ERROR: trade_type은 '매수' 또는 '매도'여야 합니다.")
            return None

        # 각 API 호출 전에 필요한 TR_ID를 헤더에 업데이트
        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = self._config['custtype']
        self._headers["gt_uid"] = os.urandom(16).hex()  # 32Byte (16바이트) 랜덤 UUID 생성

        # 바디 파라미터 설정 (문서에 따라 키는 대문자)
        data = {
            "CANO": self._config['stock_account_number'],
            "ACNT_PRDT_CD": "01",
            "PDNO": stock_code,
            "ORD_QTY": order_qty,
            "ORD_UNPR": order_price,
            "INQR_PSBL_QTY_DVN": "01",
            "ORD_DVSN": order_dvsn,
            "LOCL_CSHR_PRCS_DVSN": "00",
            "RPRS_SYS_DVSN": "00",
            "TR_DVN": trade_type
        }

        # hashkey 생성 및 헤더에 추가
        calculated_hashkey = self._get_hashkey(data)
        if not calculated_hashkey:
            return None
        self._headers["hashkey"] = calculated_hashkey

        print(f"INFO: 주식 {trade_type} 주문 시도 - 종목: {stock_code}, 수량: {order_qty}, 가격: {order_price}")
        return self._call_api('POST', path, data=data)