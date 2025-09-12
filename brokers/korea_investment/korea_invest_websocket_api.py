# api/korea_invest_websocket_api.py
import websockets  # pip install websockets
import json
import logging
import requests
import certifi  # requests의 SSL 인증서 검증에 필요
import asyncio  # 비동기 처리를 위해 필요
import os  # os.urandom (gt_uid 생성용)

from Crypto.Cipher import AES  # pip install pycryptodome
from Crypto.Util.Padding import unpad
from base64 import b64decode

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # KoreaInvestEnv 클래스 임포트


class KoreaInvestWebSocketAPI:
    """
    한국투자증권 Open API의 웹소켓 연결 및 실시간 데이터 수신을 관리하는 클래스입니다.
    `websockets` 라이브러리(asyncio 기반)를 사용하며, 다양한 실시간 데이터 파싱을 포함합니다.
    """

    def __init__(self, env: KoreaInvestApiEnv, logger=None):
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)
        # self._config = self._env.get_full_config()  # 환경 설정 전체를 가져옴 (tr_ids 포함)
        # config에서 웹소켓 및 REST API 정보 가져오기
        # self._websocket_url = self._config['websocket_url']
        # self._rest_api_key = self._config['api_key']
        # self._rest_api_secret = self._config['api_secret_key']
        # self._base_rest_url = self._config['base_url']
        self._websocket_url = None
        self._rest_api_key = None
        self._rest_api_secret = None
        self._base_rest_url = None

        self.ws = None  # 웹소켓 연결 객체 (websockets.WebSocketClientProtocol)
        self.approval_key = None  # 웹소켓 접속 키 (REST API로 발급)
        self._is_connected = False  # 웹소켓 연결 상태 플래그
        self._receive_task = None  # 메시지 수신을 위한 asyncio.Task

        # 실시간 메시지 수신 시 외부에서 등록할 콜백 함수 (TradingService의 핸들러)
        self.on_realtime_message_callback = None

        # 암호화된 체결 통보 메시지 복호화를 위한 AES 키/IV
        # H0IFCNI0, H0STCNI0, H0MFCNI0, H0EUCNI0, H0STCNI9 등 통보 TR_ID 구독 시 서버로부터 수신
        self._aes_key = None
        self._aes_iv = None

    def _aes_cbc_base64_dec(self, key, iv, cipher_text):
        """
        AES256 DECODE (Base64 인코딩된 암호문을 복호화)
        :param key: AES256 Secret Key (str)
        :param iv: AES256 Initialize Vector (str)
        :param cipher_text: Base64 인코딩된 AES256 암호문 (str)
        :return: 복호화된 문자열 (str)
        """
        try:
            cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
            return bytes.decode(unpad(cipher.decrypt(b64decode(cipher_text)), AES.block_size))
        except Exception as e:
            self._logger.error(f"AES 복호화 오류 발생: {e} (key: {key[:5]}..., iv: {iv[:5]}..., cipher: {cipher_text[:50]}...)")
            return None

    async def _get_approval_key(self):
        """
        웹소켓 접속 키(approval_key)를 한국투자증권 REST API를 통해 발급받습니다.
        """
        self._websocket_url = self._env.active_config['websocket_url']
        self._base_rest_url = self._env.active_config['base_url']
        self._rest_api_key= self._env.active_config['api_key']
        self._rest_api_secret= self._env.active_config['api_secret_key']

        path = "/oauth2/Approval"
        url = f"{self._base_rest_url}{path}"
        headers = {"content-type": "application/json; utf-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self._rest_api_key,
            "secretkey": self._rest_api_secret  # 웹소켓 접속키 발급 시 secretkey 필드명 사용
        }

        self._logger.info("웹소켓 접속키 발급 시도...")
        try:
            # requests는 동기 함수이므로 asyncio의 loop.run_in_executor를 사용하여 비동기적으로 실행
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None,  # 기본 ThreadPoolExecutor 사용
                lambda: requests.post(url, headers=headers, data=json.dumps(body), verify=certifi.where())
            )

            res.raise_for_status()  # HTTP 오류(4xx, 5xx) 발생 시 예외 발생
            auth_data = res.json()

            if auth_data and auth_data.get('approval_key'):
                self.approval_key = auth_data['approval_key']
                self._logger.info(f"웹소켓 접속키 발급 성공: {self.approval_key[:10]}...")  # 키의 일부만 로깅
                return self.approval_key
            else:
                self._logger.error(f"웹소켓 접속키 발급 실패 - 응답 데이터 오류: {auth_data}")
                return None
        except requests.exceptions.RequestException as e:
            self._logger.error(f"웹소켓 접속키 발급 중 네트워크 오류: {e}")
            return None
        except json.JSONDecodeError:
            self._logger.error(f"웹소켓 접속키 발급 응답 JSON 디코딩 실패: {res.text if res else '응답 없음'}")
            return None
        except Exception as e:
            self._logger.error(f"웹소켓 접속키 발급 중 알 수 없는 오류: {e}")
            return None

    async def _receive_messages(self):
        """웹소켓으로부터 메시지를 비동기적으로 계속 수신하는 내부 태스크."""
        try:
            while self._is_connected:
                # 메시지를 받을 때까지 대기
                message = await self.ws.recv()
                self._handle_websocket_message(message)
        except websockets.ConnectionClosedOK:
            self._logger.info("웹소켓 연결이 정상적으로 종료되었습니다.")
        except websockets.ConnectionClosedError as e:
            self._logger.error(f"웹소켓 연결이 예외적으로 종료되었습니다: {e}")
        except asyncio.CancelledError:
            self._logger.info("웹소켓 메시지 수신 태스크가 취소되었습니다.")
        except Exception as e:
            self._logger.error(f"웹소켓 메시지 수신 중 예상치 못한 오류 발생: {e}")
        finally:
            self._is_connected = False
            self.ws = None  # 웹소켓 객체 초기화 (재연결을 위해)

    def _handle_websocket_message(self, message: str):
        """수신된 웹소켓 메시지를 파싱하고 등록된 콜백으로 전달."""
        self._websocket_url = self._env.active_config['websocket_url']
        self._base_rest_url = self._env.active_config['base_url']
        self._rest_api_key= self._env.active_config['api_key']
        self._rest_api_secret= self._env.active_config['api_secret_key']

        # 한국투자증권 실시간 데이터는 '|'로 구분된 문자열 또는 JSON 객체로 수신됨
        if message and (message.startswith('0|') or message.startswith('1|')):  # 실시간 데이터 (0: 일반, 1: 체결통보)
            recvstr = message.split('|')
            tr_id = recvstr[1]  # 두 번째 요소가 TR_ID
            data_body = recvstr[3]  # 네 번째 요소가 실제 데이터 본문

            self._logger.debug(f"받은 TR_ID: {tr_id}")
            self._logger.debug(f"비교 대상: {self._env.active_config['tr_ids']['websocket']['realtime_price']}")

            parsed_data = {}
            message_type = 'unknown'

            # --- 주식 관련 실시간 데이터 파싱 ---
            if tr_id == self._env.active_config['tr_ids']['websocket']['realtime_price']:  # H0STCNT0 (주식 체결)
                parsed_data = self._parse_stock_contract_data(data_body)
                message_type = 'realtime_price'
            elif tr_id == self._env.active_config['tr_ids']['websocket']['realtime_quote']:  # H0STASP0 (주식 호가)
                parsed_data = self._parse_stock_quote_data(data_body)
                message_type = 'realtime_quote'

            # --- 파생상품 및 기타 실시간 데이터 파싱 (제공된 예제 코드 기반) ---
            # 각 TR_ID에 따라 정확한 파싱 함수 호출
            elif tr_id == "H0IFASP0" or tr_id == "H0IOASP0":  # 지수선물/옵션 호가
                parsed_data = self._parse_futs_optn_quote_data(data_body)
                message_type = 'realtime_futs_optn_quote'
            elif tr_id == "H0IFCNT0" or tr_id == "H0IOCNT0":  # 지수선물/옵션 체결
                parsed_data = self._parse_futs_optn_contract_data(data_body)
                message_type = 'realtime_futs_optn_contract'
            elif tr_id == "H0CFASP0":  # 상품선물 호가
                parsed_data = self._parse_product_futs_quote_data(data_body)
                message_type = 'realtime_product_futs_quote'
            elif tr_id == "H0CFCNT0":  # 상품선물 체결
                parsed_data = self._parse_product_futs_contract_data(data_body)
                message_type = 'realtime_product_futs_contract'
            elif tr_id == "H0ZFASP0" or tr_id == "H0ZOASP0":  # 주식선물/옵션 호가
                parsed_data = self._parse_stock_futs_optn_quote_data(data_body)
                message_type = 'realtime_stock_futs_optn_quote'
            elif tr_id == "H0ZFCNT0" or tr_id == "H0ZOCNT0":  # 주식선물/옵션 체결
                parsed_data = self._parse_stock_futs_optn_contract_data(data_body)
                message_type = 'realtime_stock_futs_optn_contract'
            elif tr_id == "H0ZFANC0" or tr_id == "H0ZOANC0":  # 주식선물/옵션 예상체결
                parsed_data = self._parse_stock_futs_optn_exp_contract_data(data_body)
                message_type = 'realtime_stock_futs_optn_exp_contract'
            elif tr_id == "H0MFASP0":  # 야간선물(CME) 호가
                parsed_data = self._parse_cmefuts_quote_data(data_body)
                message_type = 'realtime_cmefuts_quote'
            elif tr_id == "H0MFCNT0":  # 야간선물(CME) 체결
                parsed_data = self._parse_cmefuts_contract_data(data_body)
                message_type = 'realtime_cmefuts_contract'
            elif tr_id == "H0EUASP0":  # 야간옵션(EUREX) 호가
                parsed_data = self._parse_eurex_optn_quote_data(data_body)
                message_type = 'realtime_eurex_optn_quote'
            elif tr_id == "H0EUCNT0":  # 야간옵션(EUREX) 체결
                parsed_data = self._parse_eurex_optn_contract_data(data_body)
                message_type = 'realtime_eurex_optn_contract'
            elif tr_id == "H0EUANC0":  # 야간옵션(EUREX) 예상체결
                parsed_data = self._parse_eurex_optn_exp_contract_data(data_body)
                message_type = 'realtime_eurex_optn_exp_contract'

            # --- 체결/주문 통보 (암호화됨) ---
            elif tr_id in ["H0STCNI0", "H0STCNI9", "H0IFCNI0", "H0MFCNI0", "H0EUCNI0"]:  # 모든 체결 통보 TR_ID
                if self._aes_key and self._aes_iv:
                    decrypted_str = self._aes_cbc_base64_dec(self._aes_key, self._aes_iv, data_body)
                    if decrypted_str:
                        parsed_data = self._parse_signing_notice(decrypted_str, tr_id)
                        message_type = 'signing_notice'
                    else:
                        self._logger.error(f"체결통보 복호화 실패: {tr_id}, 데이터: {data_body[:50]}...")
                        return
                else:
                    self._logger.warning(f"체결통보 암호화 해제 실패: AES 키/IV 없음. TR_ID: {tr_id}, 메시지: {message[:50]}...")
                    return

            elif tr_id == self._env.active_config['tr_ids']['websocket'].get('realtime_program_trading', 'H0STPGM0'):
                parsed_data = self._parse_program_trading_data(data_body)
                message_type = 'realtime_program_trading'

            # 외부 콜백 함수로 파싱된 데이터 전달
            if self.on_realtime_message_callback:
                self.on_realtime_message_callback({'type': message_type, 'tr_id': tr_id, 'data': parsed_data})

        else:  # 제어 메시지 (응답, PINGPONG 등)
            try:
                json_object = json.loads(message)
                header = json_object.get("header", {})
                tr_id = header.get("tr_id")

                if tr_id == "PINGPONG":
                    self._logger.info("PINGPONG 수신됨. PONG 응답.")
                    # websockets 라이브러리 내부에서 PONG 응답 자동 처리 (ping_interval, ping_timeout 설정 시)
                elif json_object.get("body", {}).get("rt_cd") == '0':
                    self._logger.info(f"실시간 요청 응답 성공: TR_KEY={header.get('tr_key')}, MSG={json_object['body']['msg1']}")
                    # 체결통보용 AES KEY, IV 수신 처리
                    if tr_id in ["H0IFCNI0", "H0STCNI0", "H0STCNI9", "H0MFCNI0", "H0EUCNI0"] and json_object.get("body",
                                                                                                                 {}).get(
                            "output"):
                        self._aes_key = json_object["body"]["output"].get("key")
                        self._aes_iv = json_object["body"]["output"].get("iv")
                        self._logger.info(f"체결통보용 AES KEY/IV 수신 성공. TRID={tr_id}")
                else:
                    self._logger.error(
                        f"실시간 요청 응답 오류: TR_KEY={header.get('tr_key')}, RT_CD={json_object.get('body', {}).get('rt_cd')}, MSG={json_object.get('body', {}).get('msg1')}")
                    if json_object.get("body", {}).get("msg1") == 'ALREADY IN SUBSCRIBE':
                        self._logger.warning("이미 구독 중인 종목입니다.")
            except json.JSONDecodeError:
                self._logger.error(f"제어 메시지 JSON 디코딩 실패: {message}")
            except Exception as e:
                self._logger.error(f"제어 메시지 처리 중 오류 발생: {e}, 메시지: {message}")

    # --- 실시간 데이터 파싱 헬퍼 함수들 ---

    def _parse_stock_quote_data(self, data_str):
        """H0STASP0 (주식 호가) 데이터를 파싱합니다."""
        recvvalue = data_str.split('^')
        # API 문서: 주식호가 (H0STASP0) 필드명과 인덱스 참고
        return {
            "유가증권단축종목코드": recvvalue[0], "영업시간": recvvalue[1], "시간구분코드": recvvalue[2],
            "매도호가1": recvvalue[3], "매도호가2": recvvalue[4], "매도호가3": recvvalue[5], "매도호가4": recvvalue[6],
            "매도호가5": recvvalue[7],
            "매도호가6": recvvalue[8], "매도호가7": recvvalue[9], "매도호가8": recvvalue[10], "매도호가9": recvvalue[11],
            "매도호가10": recvvalue[12],
            "매수호가1": recvvalue[13], "매수호가2": recvvalue[14], "매수호가3": recvvalue[15], "매수호가4": recvvalue[16],
            "매수호가5": recvvalue[17],
            "매수호가6": recvvalue[18], "매수호가7": recvvalue[19], "매수호가8": recvvalue[20], "매수호가9": recvvalue[21],
            "매수호가10": recvvalue[22],
            "매도호가잔량1": recvvalue[23], "매도호가잔량2": recvvalue[24], "매도호가잔량3": recvvalue[25], "매도호가잔량4": recvvalue[26],
            "매도호가잔량5": recvvalue[27],
            "매도호가잔량6": recvvalue[28], "매도호가잔량7": recvvalue[29], "매도호가잔량8": recvvalue[30], "매도호가잔량9": recvvalue[31],
            "매도호가잔량10": recvvalue[32],
            "매수호가잔량1": recvvalue[33], "매수호가잔량2": recvvalue[34], "매수호가잔량3": recvvalue[35], "매수호가잔량4": recvvalue[36],
            "매수호가잔량5": recvvalue[37],
            "매수호가잔량6": recvvalue[38], "매수호가잔량7": recvvalue[39], "매수호가잔량8": recvvalue[40], "매수호가잔량9": recvvalue[41],
            "매수호가잔량10": recvvalue[42],
            "총매도호가잔량": recvvalue[43], "총매수호가잔량": recvvalue[44], "시간외총매도호가잔량": recvvalue[45],
            "시간외총매수호가잔량": recvvalue[46],
            "예상체결가": recvvalue[47], "예상체결량": recvvalue[48], "예상거래량": recvvalue[49], "예상체결대비": recvvalue[50],
            "부호": recvvalue[51],
            "예상체결전일대비율": recvvalue[52], "누적거래량": recvvalue[53], "주식매매구분코드": recvvalue[58]
        }

    def _parse_stock_contract_data(self, data_str):
        """H0STCNT0 (주식 체결) 데이터를 파싱합니다."""
        menulist = "유가증권단축종목코드|주식체결시간|주식현재가|전일대비부호|전일대비|전일대비율|가중평균주식가격|주식시가|주식최고가|주식최저가|매도호가1|매수호가1|체결거래량|누적거래량|누적거래대금|매도체결건수|매수체결건수|순매수체결건수|체결강도|총매도수량|총매수수량|체결구분|매수비율|전일거래량대비등락율|시가시간|시가대비구분|시가대비|최고가시간|고가대비구분|고가대비|최저가시간|저가대비구분|저가대비|영업일자|신장운영구분코드|거래정지여부|매도호가잔량|매수호가잔량|총매도호가잔량|총매수호가잔량|거래량회전율|전일동시간누적거래량|전일동시간누적거래량비율|시간구분코드|임의종료구분코드|정적VI발동기준가"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_futs_optn_quote_data(self, data_str):
        """H0IFASP0, H0IOASP0 (지수선물/옵션 호가) 데이터를 파싱합니다."""
        recvvalue = data_str.split('^')
        return {
            "종목코드": recvvalue[0], "영업시간": recvvalue[1],
            "매도호가1": recvvalue[2], "매도호가2": recvvalue[3], "매도호가3": recvvalue[4], "매도호가4": recvvalue[5],
            "매도호가5": recvvalue[6],
            "매수호가1": recvvalue[7], "매수호가2": recvvalue[8], "매수호가3": recvvalue[9], "매수호가4": recvvalue[10],
            "매수호가5": recvvalue[11],
            "매도호가건수1": recvvalue[12], "매도호가건수2": recvvalue[13], "매도호가건수3": recvvalue[14], "매도호가건수4": recvvalue[15],
            "매도호가건수5": recvvalue[16],
            "매수호가건수1": recvvalue[17], "매수호가건수2": recvvalue[18], "매수호가건수3": recvvalue[19], "매수호가건수4": recvvalue[20],
            "매수호가건수5": recvvalue[21],
            "매도호가잔량1": recvvalue[22], "매도호가잔량2": recvvalue[23], "매도호가잔량3": recvvalue[24], "매도호가잔량4": recvvalue[25],
            "매도호가잔량5": recvvalue[26],
            "매수호가잔량1": recvvalue[27], "매수호가잔량2": recvvalue[28], "매수호가잔량3": recvvalue[29], "매수호가잔량4": recvvalue[30],
            "매수호가잔량5": recvvalue[31],
            "총매도호가건수": recvvalue[32], "총매수호가건수": recvvalue[33],
            "총매도호가잔량": recvvalue[34], "총매수호가잔량": recvvalue[35],
            "총매도호가잔량증감": recvvalue[36], "총매수호가잔량증감": recvvalue[37]
        }

    def _parse_futs_optn_contract_data(self, data_str):
        """H0IFCNT0, H0IOCNT0 (지수선물/옵션 체결) 데이터를 파싱합니다."""
        menulist = "선물단축종목코드|영업시간|선물전일대비|전일대비부호|선물전일대비율|선물현재가|선물시가|선물최고가|선물최저가|최종거래량|누적거래량|누적거래대금|HTS이론가|시장베이시스|괴리율|근월물약정가|원월물약정가|스프레드|미결제약정수량|미결제약정수량증감|시가시간|시가대비현재가부호|시가대비지수현재가|최고가시간|최고가대비현재가부호|최고가대비지수현재가|최저가시간|최저가대비현재가부호|최저가대비지수현재가|매수비율|체결강도|괴리도|미결제약정직전수량증감|이론베이시스|선물매도호가|선물매수호가|매도호가잔량|매수호가잔량|매도체결건수|매수체결건수|순매수체결건수|총매도수량|총매수수량|총매도호가잔량|총매수호가잔량|전일거래량대비등락율|협의대량거래량|실시간상한가|실시간하한가|실시간가격제한구분"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_product_futs_quote_data(self, data_str):
        """H0CFASP0 (상품선물 호가) 데이터를 파싱합니다."""
        recvvalue = data_str.split('^')
        return {
            "종목코드": recvvalue[0], "영업시간": recvvalue[1],
            "매도호가1": recvvalue[2], "매도호가2": recvvalue[3], "매도호가3": recvvalue[4], "매도호가4": recvvalue[5],
            "매도호가5": recvvalue[6],
            "매수호가1": recvvalue[7], "매수호가2": recvvalue[8], "매수호가3": recvvalue[9], "매수호가4": recvvalue[10],
            "매수호가5": recvvalue[11],
            "매도호가건수1": recvvalue[12], "매도호가건수2": recvvalue[13], "매도호가건수3": recvvalue[14], "매도호가건수4": recvvalue[15],
            "매도호가건수5": recvvalue[16],
            "매수호가건수1": recvvalue[17], "매수호가건수2": recvvalue[18], "매수호가건수3": recvvalue[19], "매수호가건수4": recvvalue[20],
            "매수호가건수5": recvvalue[21],
            "매도호가잔량1": recvvalue[22], "매도호가잔량2": recvvalue[23], "매도호가잔량3": recvvalue[24], "매도호가잔량4": recvvalue[25],
            "매도호가잔량5": recvvalue[26],
            "매수호가잔량1": recvvalue[27], "매수호가잔량2": recvvalue[28], "매수호가잔량3": recvvalue[29], "매수호가잔량4": recvvalue[30],
            "매수호가잔량5": recvvalue[31],
            "총매도호가건수": recvvalue[32], "총매수호가건수": recvvalue[33],
            "총매도호가잔량": recvvalue[34], "총매수호가잔량": recvvalue[35],
            "총매도호가잔량증감": recvvalue[36], "총매수호가잔량증감": recvvalue[37]
        }

    def _parse_product_futs_contract_data(self, data_str):
        """H0CFCNT0 (상품선물 체결) 데이터를 파싱합니다."""
        menulist = "선물단축종목코드|영업시간|선물전일대비|전일대비부호|선물전일대비율|선물현재가|선물시가|선물최고가|선물최저가|최종거래량|누적거래량|누적거래대금|HTS이론가|시장베이시스|괴리율|근월물약정가|원월물약정가|스프레드|미결제약정수량|미결제약정수량증감|시가시간|시가대비현재가부호|시가대비지수현재가|최고가시간|최고가대비현재가부호|최고가대비지수현재가|최저가시간|최저가대비현재가부호|최저가대비지수현재가|매수비율|체결강도|괴리도|미결제약정직전수량증감|이론베이시스|선물매도호가|선물매수호가|매도호가잔량|매수호가잔량|매도체결건수|매수체결건수|순매수체결건수|총매도수량|총매수수량|총매도호가잔량|총매수호가잔량|전일거래량대비등락율|협의대량거래량|실시간상한가|실시간하한가|실시간가격제한구분"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_stock_futs_optn_quote_data(self, data_str):
        """H0ZFASP0, H0ZOASP0 (주식선물/옵션 호가) 데이터를 파싱합니다."""
        recvvalue = data_str.split('^')
        return {
            "종목코드": recvvalue[0], "영업시간": recvvalue[1],
            "매도호가1": recvvalue[2], "매도호가2": recvvalue[3], "매도호가3": recvvalue[4], "매도호가4": recvvalue[5],
            "매도호가5": recvvalue[6],
            "매수호가1": recvvalue[7], "매수호가2": recvvalue[8], "매수호가3": recvvalue[9], "매수호가4": recvvalue[10],
            "매수호가5": recvvalue[11],
            "매도호가건수1": recvvalue[12], "매도호가건수2": recvvalue[13], "매도호가건수3": recvvalue[14], "매도호가건수4": recvvalue[15],
            "매도호가건수5": recvvalue[16],
            "매수호가건수1": recvvalue[17], "매수호가건수2": recvvalue[18], "매수호가건수3": recvvalue[19], "매수호가건수4": recvvalue[20],
            "매수호가건수5": recvvalue[21],
            "매도호가잔량1": recvvalue[22], "매도호가잔량2": recvvalue[23], "매도호가잔량3": recvvalue[24], "매도호가잔량4": recvvalue[25],
            "매도호가잔량5": recvvalue[26],
            "매수호가잔량1": recvvalue[27], "매수호가잔량2": recvvalue[28], "매수호가잔량3": recvvalue[29], "매수호가잔량4": recvvalue[30],
            "매수호가잔량5": recvvalue[31],
            "총매도호가건수": recvvalue[32], "총매수호가건수": recvvalue[33],
            "총매도호가잔량": recvvalue[34], "총매수호가잔량": recvvalue[35],
            "총매도호가잔량증감": recvvalue[36], "총매수호가잔량증감": recvvalue[37]
        }

    def _parse_stock_futs_optn_contract_data(self, data_str):
        """H0ZFCNT0, H0ZOCNT0 (주식선물/옵션 체결) 데이터를 파싱합니다."""
        menulist = "선물단축종목코드|영업시간|주식현재가|전일대비부호|전일대비|선물전일대비율|주식시가2|주식최고가|주식최저가|최종거래량|누적거래량|누적거래대금|HTS이론가|시장베이시스|괴리율|근월물약정가|원월물약정가|스프레드1|HTS미결제약정수량|미결제약정수량증감|시가시간|시가2대비현재가부호|시가2대비현재가|최고가시간|최고가대비현재가부호|최고가대비현재가|최저가시간|최저가대비현재가부호|최저가대비현재가|매수2비율|체결강도|괴리도|미결제약정직전수량증감|이론베이시스|매도호가1|매수호가1|매도호가잔량1|매수호가잔량1|매도체결건수|매수체결건수|순매수체결건수|총매도수량|총매수수량|총매도호가잔량|총매수호가잔량|전일거래량대비등락율|실시간상한가|실시간하한가|실시간가격제한구분"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_stock_futs_optn_exp_contract_data(self, data_str):
        """H0ZFANC0, H0ZOANC0 (주식선물/옵션 예상체결) 데이터를 파싱합니다."""
        menulist = "선물단축종목코드|영업시간|예상체결가|예상체결대비|예상체결대비부호|예상체결전일대비율|예상장운영구분코드"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_cmefuts_quote_data(self, data_str):
        """H0MFASP0 (야간선물(CME) 호가) 데이터를 파싱합니다."""
        recvvalue = data_str.split('^')
        return {
            "종목코드": recvvalue[0], "영업시간": recvvalue[1],
            "매도호가1": recvvalue[2], "매도호가2": recvvalue[3], "매도호가3": recvvalue[4], "매도호가4": recvvalue[5],
            "매도호가5": recvvalue[6],
            "매수호가1": recvvalue[7], "매수호가2": recvvalue[8], "매수호가3": recvvalue[9], "매수호가4": recvvalue[10],
            "매수호가5": recvvalue[11],
            "매도호가건수1": recvvalue[12], "매도호가건수2": recvvalue[13], "매도호가건수3": recvvalue[14], "매도호가건수4": recvvalue[15],
            "매도호가건수5": recvvalue[16],
            "매수호가건수1": recvvalue[17], "매수호가건수2": recvvalue[18], "매수호가건수3": recvvalue[19], "매수호가건수4": recvvalue[20],
            "매수호가건수5": recvvalue[21],
            "매도호가잔량1": recvvalue[22], "매도호가잔량2": recvvalue[23], "매도호가잔량3": recvvalue[24], "매도호가잔량4": recvvalue[25],
            "매도호가잔량5": recvvalue[26],
            "매수호가잔량1": recvvalue[27], "매수호가잔량2": recvvalue[28], "매수호가잔량3": recvvalue[29], "매수호가잔량4": recvvalue[30],
            "매수호가잔량5": recvvalue[31],
            "총매도호가건수": recvvalue[32], "총매수호가건수": recvvalue[33],
            "총매도호가잔량": recvvalue[34], "총매수호가잔량": recvvalue[35],
            "총매도호가잔량증감": recvvalue[36], "총매수호가잔량증감": recvvalue[37]
        }

    def _parse_cmefuts_contract_data(self, data_str):
        """H0MFCNT0 (야간선물(CME) 체결) 데이터를 파싱합니다."""
        menulist = "선물단축종목코드|영업시간|선물전일대비|전일대비부호|선물전일대비율|선물현재가|선물시가2|선물최고가|선물최저가|최종거래량|누적거래량|누적거래대금|HTS이론가|시장베이시스|괴리율|근월물약정가|원월물약정가|스프레드1|HTS미결제약정수량|미결제약정수량증감|시가시간|시가2대비현재가부호|시가2대비현재가|최고가시간|최고가대비현재가부호|최고가대비현재가|최저가시간|최저가대비현재가부호|최저가대비현재가|매수2비율|체결강도|괴리도|미결제약정직전수량증감|이론베이시스|선물매도호가1|선물매수호가1|매도호가잔량1|매수호가잔량1|매도체결건수|매수체결건수|순매수체결건수|총매도수량|총매수수량|총매도호가잔량|총매수호가잔량|전일거래량대비등락율"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_eurex_optn_quote_data(self, data_str):
        """H0EUASP0 (야간옵션(EUREX) 호가) 데이터를 파싱합니다."""
        recvvalue = data_str.split('^')
        return {
            "종목코드": recvvalue[0], "영업시간": recvvalue[1],
            "매도호가1": recvvalue[2], "매도호가2": recvvalue[3], "매도호가3": recvvalue[4], "매도호가4": recvvalue[5],
            "매도호가5": recvvalue[6],
            "매수호가1": recvvalue[7], "매수호가2": recvvalue[8], "매수호가3": recvvalue[9], "매수호가4": recvvalue[10],
            "매수호가5": recvvalue[11],
            "매도호가건수1": recvvalue[12], "매도호가건수2": recvvalue[13], "매도호가건수3": recvvalue[14], "매도호가건수4": recvvalue[15],
            "매도호가건수5": recvvalue[16],
            "매수호가건수1": recvvalue[17], "매수호가건수2": recvvalue[18], "매수호가건수3": recvvalue[19], "매수호가건수4": recvvalue[20],
            "매수호가건수5": recvvalue[21],
            "매도호가잔량1": recvvalue[22], "매도호가잔량2": recvvalue[23], "매도호가잔량3": recvvalue[24], "매도호가잔량4": recvvalue[25],
            "매도호가잔량5": recvvalue[26],
            "매수호가잔량1": recvvalue[27], "매수호가잔량2": recvvalue[28], "매수호가잔량3": recvvalue[29], "매수호가잔량4": recvvalue[30],
            "매수호가잔량5": recvvalue[31],
            "총매도호가건수": recvvalue[32], "총매수호가건수": recvvalue[33],
            "총매도호가잔량": recvvalue[34], "총매수호가잔량": recvvalue[35],
            "총매도호가잔량증감": recvvalue[36], "총매수호가잔량증감": recvvalue[37]
        }

    def _parse_eurex_optn_contract_data(self, data_str):
        """H0EUCNT0 (야간옵션(EUREX) 체결) 데이터를 파싱합니다."""
        menulist = "옵션단축종목코드|영업시간|옵션현재가|전일대비부호|옵션전일대비|전일대비율|옵션시가2|옵션최고가|옵션최저가|최종거래량|누적거래량|누적거래대금|HTS이론가|HTS미결제약정수량|미결제약정수량증감|시가시간|시가2대비현재가부호|시가대비지수현재가|최고가시간|최고가대비현재가부호|최고가대비지수현재가|최저가시간|최저가대비현재가부호|최저가대비지수현재가|매수2비율|프리미엄값|내재가치값|시간가치값|델타|감마|베가|세타|로우|HTS내재변동성|괴리도|미결제약정직전수량증감|이론베이시스|역사적변동성|체결강도|괴리율|시장베이시스|옵션매도호가1|옵션매수호가1|매도호가잔량1|매수호가잔량1|매도체결건수|매수체결건수|순매수체결건수|총매도수량|총매수수량|총매도호가잔량|총매수호가잔량|전일거래량대비등락율"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _parse_eurex_optn_exp_contract_data(self, data_str):
        """H0EUANC0 (야간옵션(EUREX) 예상체결) 데이터를 파싱합니다."""
        menulist = "옵션단축종목코드|영업시간|예상체결가|예상체결대비|예상체결대비부호|예상체결전일대비율|예상장운영구분코드"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    # === 1) 파싱 유틸 ===
    def _parse_program_trading_data(self, data_str: str) -> dict:
        """
        H0STPGM0 (국내주식 실시간 프로그램매매 KRX) 데이터 파싱
        엑셀 명세 열 순서:
        STCK_CNTG_HOUR, SELN_CNQN, SELN_TR_PBMN, SHNU_CNQN, SHNU_TR_PBMN,
        NTBY_CNQN, NTBY_TR_PBMN, SELN_RSQN, SHNU_RSQN, WHOL_NTBY_QTY
        """
        recv = data_str.split('^')
        # 안전 가드
        while len(recv) < 10:
            recv.append("")

        return {
            # 체결시각 (HHMMSS)
            "STCK_CNTG_HOUR": recv[0],

            # 매도/매수 체결 건수, 거래대금
            "SELN_CNQN": recv[1],  # 매도 체결건수
            "SELN_TR_PBMN": recv[2],  # 매도 거래대금
            "SHNU_CNQN": recv[3],  # 매수 체결건수
            "SHNU_TR_PBMN": recv[4],  # 매수 거래대금

            # 순매수 건수/거래대금
            "NTBY_CNQN": recv[5],  # 순매수 체결건수
            "NTBY_TR_PBMN": recv[6],  # 순매수 거래대금

            # 매도/매수 잔량, 전체 순매수 수량
            "SELN_RSQN": recv[7],  # 매도 잔량
            "SHNU_RSQN": recv[8],  # 매수 잔량
            "WHOL_NTBY_QTY": recv[9],  # 전체 순매수 수량
        }

    # === 2) 구독/해지 메서드 ===
    async def subscribe_program_trading(self, stock_code: str):
        """
        국내주식 실시간 프로그램매매 동향 (H0STPGM0) 구독
        :param stock_code: 종목코드(단축)
        """
        tr_id = self._env.active_config['tr_ids']['websocket'].get('realtime_program_trading', 'H0STPGM0')
        self._logger.info(f"[프로그램매매] 종목 {stock_code} 구독 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="1")

    async def unsubscribe_program_trading(self, stock_code: str):
        """
        국내주식 실시간 프로그램매매 동향 (H0STPGM0) 구독 해지
        """
        tr_id = self._env.active_config['tr_ids']['websocket'].get('realtime_program_trading', 'H0STPGM0')
        self._logger.info(f"[프로그램매매] 종목 {stock_code} 구독 해지 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="2")

    # --- 웹소켓 연결 및 해지 ---
    async def connect(self, on_message_callback=None):
        """웹소켓 연결을 시작하고 실시간 데이터 수신을 준비합니다."""
        self._websocket_url = self._env.get_websocket_url()
        if self.ws and self._is_connected:
            self._logger.info("웹소켓이 이미 연결되어 있습니다.")
            return True

        self.on_realtime_message_callback = on_message_callback  # 외부 콜백 등록

        # 1. approval_key 발급 (비동기)
        if not self.approval_key:
            self.approval_key = await self._get_approval_key()
            if not self.approval_key:
                self._logger.error("웹소켓 접속 키 발급 실패로 연결할 수 없습니다.")
                raise RuntimeError("approval_key 발급 실패")  # 추가

        # 2. 웹소켓 연결 (async with 사용)
        try:
            self._logger.info(f"웹소켓 연결 시작: {self._websocket_url}")
            self.ws = await websockets.connect(self._websocket_url, ping_interval=20, ping_timeout=20)
            self._is_connected = True
            self._logger.info("웹소켓 연결 성공.")

            # 메시지 수신 태스크 시작 (백그라운드에서 계속 메시지 받기)
            self._receive_task = asyncio.create_task(self._receive_messages())

            return True
        except Exception as e:
            self._logger.error(f"웹소켓 연결 중 오류 발생: {e}")
            self._is_connected = False
            self.ws = None
            return False

    async def disconnect(self):
        """웹소켓 연결을 종료합니다."""
        if self._is_connected and self.ws:
            self._logger.info("웹소켓 연결 종료 요청.")
            await self.ws.close()
            self._is_connected = False
            if self._receive_task:
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    self._logger.info("웹소켓 수신 태스크 취소됨.")
                except Exception as e:
                    self._logger.error(f"웹소켓 수신 태스크 종료 중 오류: {e}")
            self._logger.info("웹소켓 연결 종료 완료.")
            self._is_connected = False
            self.ws = None
        else:
            self._logger.info("웹소켓이 연결되어 있지 않습니다.")
            self._is_connected = False
            self.ws = None

    # --- 실시간 요청 전송 ---
    async def send_realtime_request(self, tr_id, tr_key, tr_type="1"):
        """
        실시간 데이터 구독/해지 요청 메시지를 웹소켓으로 전송합니다.
        :param tr_id: 실시간 TR ID
        :param tr_key: 구독할 종목코드 또는 HTS ID (체결통보용)
        :param tr_type: 1: 등록, 2: 해지
        """
        self._websocket_url = self._env.active_config['websocket_url']
        self._base_rest_url = self._env.active_config['base_url']
        self._rest_api_key= self._env.active_config['api_key']
        self._rest_api_secret= self._env.active_config['api_secret_key']

        if not self._is_connected or not self.ws:
            self._logger.error("웹소켓이 연결되어 있지 않아 실시간 요청을 보낼 수 없습니다.")
            return False
        if not self.approval_key:
            self._logger.error("approval_key가 없어 실시간 요청을 보낼 수 없습니다.")
            return False

        header = {
            "approval_key": self.approval_key,
            "custtype": self._env.active_config['custtype'],
            "id": tr_id,
            "pwd": "",  # 빈 값
            "gt_uid": os.urandom(16).hex()  # 32Byte UUID
        }
        body = {
            "input": {
                "tr_id": tr_id,
                "tr_key": tr_key,
                "rt_type": tr_type
            }
        }

        request_message = [header, body]
        message_json = json.dumps(request_message)

        self._logger.info(f"실시간 요청 전송: TR_ID={tr_id}, TR_KEY={tr_key}, TYPE={tr_type}")
        try:
            await self.ws.send(message_json)
            return True
        except Exception as e:
            self._logger.error(f"실시간 요청 전송 중 오류 발생: {e}")
            self._is_connected = False
            self.ws = None
            return False

    async def subscribe_realtime_price(self, stock_code):
        """실시간 주식체결 데이터(현재가)를 구독합니다."""
        tr_id = self._env.active_config['tr_ids']['websocket']['realtime_price']
        self._logger.info(f"종목 {stock_code} 실시간 체결 데이터 구독 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="1")

    async def unsubscribe_realtime_price(self, stock_code):
        """실시간 주식체결 데이터(현재가) 구독을 해지합니다."""
        tr_id = self._env.active_config['tr_ids']['websocket']['realtime_price']
        self._logger.info(f"종목 {stock_code} 실시간 체결 데이터 구독 해지 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="2")

    async def subscribe_realtime_quote(self, stock_code):
        """실시간 주식호가 데이터를 구독합니다."""
        tr_id = self._env.active_config['tr_ids']['websocket']['realtime_quote']
        self._logger.info(f"종목 {stock_code} 실시간 호가 데이터 구독 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="1")

    async def unsubscribe_realtime_quote(self, stock_code):
        """실시간 주식호가 데이터 구독을 해지합니다."""
        tr_id = self._env.active_config['tr_ids']['websocket']['realtime_quote']
        self._logger.info(f"종목 {stock_code} 실시간 호가 데이터 구독 해지 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="2")

    # For test only
    async def _on_receive(self, message):
        try:
            parsed = json.loads(message)  # <-- JSON 문자열을 dict로 파싱
            if self.on_realtime_message_callback:
                await self.on_realtime_message_callback(parsed)
            else:
                self._logger.warning("수신된 메시지를 처리할 콜백이 등록되지 않았습니다.")
        except Exception as e:
            self._logger.error(f"수신 메시지 처리 중 예외 발생: {e}")
