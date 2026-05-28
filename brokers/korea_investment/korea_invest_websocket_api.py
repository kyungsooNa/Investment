# api/korea_invest_websocket_api.py
from typing import Optional, TYPE_CHECKING
import websockets  # pip install websockets
import json
try:
    import orjson as _orjson
    def _loads(s): return _orjson.loads(s)
    def _dumps(obj) -> str: return _orjson.dumps(obj).decode()
except ImportError:
    _orjson = None
    _loads = json.loads
    _dumps = json.dumps
import logging
import requests
import certifi  # requests의 SSL 인증서 검증에 필요
import asyncio  # 비동기 처리를 위해 필요
import os  # os.urandom (gt_uid 생성용)

from Crypto.Cipher import AES  # pip install pycryptodome
from Crypto.Util.Padding import unpad
from base64 import b64decode

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # KoreaInvestEnv 클래스 임포트
from core.market_clock import MarketClock
from services.market_calendar_service import MarketCalendarService

if TYPE_CHECKING:
    from core.logger import StreamingEventLogger


class KoreaInvestWebSocketAPI:
    """
    한국투자증권 Open API의 웹소켓 연결 및 실시간 데이터 수신을 관리하는 클래스입니다.
    `websockets` 라이브러리(asyncio 기반)를 사용하며, 다양한 실시간 데이터 파싱을 포함합니다.
    """

    def __init__(self, env: KoreaInvestApiEnv, logger=None, market_clock: MarketClock = None,
                 market_calendar_service: Optional[MarketCalendarService] = None,
                 streaming_logger: Optional["StreamingEventLogger"] = None):
        self._env = env
        self._market_clock = market_clock
        self._mcs = market_calendar_service
        self._logger = logger if logger else logging.getLogger(__name__)
        self._streaming_logger = streaming_logger  # 구조화 이벤트 로거 (선택적)
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
        self._auto_reconnect = False  # 자동 재연결 활성화 플래그
        self._receive_task = None  # 메시지 수신을 위한 asyncio.Task

        # 실시간 메시지 수신 시 외부에서 등록할 콜백 함수 (TradingService의 핸들러)
        self.on_realtime_message_callback = None

        # 암호화된 체결 통보 메시지 복호화를 위한 AES 키/IV
        # H0IFCNI0, H0STCNI0, H0MFCNI0, H0EUCNI0, H0STCNI9 등 통보 TR_ID 구독 시 서버로부터 수신
        self._aes_key = None
        self._aes_iv = None

        # 재연결 시 복구를 위한 구독 목록 저장소 set((tr_id, tr_key))
        self._subscribed_items = set()
        self._pending_requests = {}

        # 서버가 appkey 중복 사용을 거부한 경우 True → 다음 재연결 시 긴 대기 적용
        self._appkey_collision = False

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
            self._logger.exception(f"AES 복호화 오류 발생: {e} (key: {key[:5]}..., iv: {iv[:5]}..., cipher: {cipher_text[:50]}...)")

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
                self._logger.exception(f"웹소켓 접속키 발급 실패 - 응답 데이터 오류: {auth_data}")
                return None
        except requests.exceptions.RequestException as e:
            self._logger.exception(f"웹소켓 접속키 발급 중 네트워크 오류: {e}")
            return None
        except json.JSONDecodeError:
            self._logger.exception(f"웹소켓 접속키 발급 응답 JSON 디코딩 실패: {res.text if res else '응답 없음'}")
            return None
        except Exception as e:
            self._logger.exception(f"웹소켓 접속키 발급 중 알 수 없는 오류: {e}")
            return None

    async def _establish_connection(self):
        """웹소켓 연결을 수립하는 내부 메서드 (재연결 로직에서 재사용)."""
        self._websocket_url = self._env.get_websocket_url()
        
        # 1. approval_key 발급 (없으면 발급)
        if not self.approval_key:
            self.approval_key = await self._get_approval_key()
            if not self.approval_key:
                self._logger.error("웹소켓 접속 키 발급 실패로 연결할 수 없습니다.")
                return False

        # 2. 웹소켓 연결
        try:
            self._logger.info(f"웹소켓 연결 시도: {self._websocket_url}")
            self.ws = await websockets.connect(self._websocket_url, ping_interval=20, ping_timeout=20)
            self._is_connected = True
            self._logger.info("웹소켓 연결 성공.")
            return True
        except Exception as e:
            self._logger.exception(f"웹소켓 연결 중 오류 발생: {e}")
            self._is_connected = False
            self.ws = None
            return False

    async def _receive_messages(self):
        """웹소켓 메시지 수신 및 자동 재연결 루프."""
        retry_count = 0
        appkey_collision_count = 0   # appkey 충돌 연속 횟수
        max_retries = 30     # 최대 재시도 횟수 (약 30분간 시도)
        base_delay = 3       # 기본 대기 시간 (초)
        max_delay = 60       # 일반 재연결 최대 대기 시간 (초)
        max_appkey_collision_delay = 120  # appkey 충돌 시 최대 대기 시간 (초) — 서버 세션 해제에 최대 90초 소요
        DATA_TIMEOUT = 60.0  # [추가] 데이터 수신 타임아웃 (초)

        while self._auto_reconnect:
            # 1. 연결이 끊겨있다면 재연결 시도
            if not self._is_connected:
                # 장 운영 시간 확인 (MarketCalendarService가 있을 경우)
                market_is_open = True
                if self._mcs:
                    market_is_open = await self._mcs.is_market_open_now()
                if not market_is_open:
                    self._logger.info("장이 종료되어 자동 재연결을 중단합니다.")
                    self._auto_reconnect = False
                    break

                if retry_count >= max_retries:
                    self._logger.error(f"웹소켓 재연결 실패: 최대 재시도 횟수({max_retries})를 초과했습니다.")
                    self._auto_reconnect = False
                    break

                was_appkey_collision = self._appkey_collision
                if was_appkey_collision:
                    # 서버가 appkey 중복 사용 거부 → 충돌 횟수에 따라 대기 시간 누적 증가
                    # 서버 세션 해제에 최대 90초 소요될 수 있으므로 첫 시도부터 60초 이상 대기
                    appkey_collision_count += 1
                    delay = min(max_appkey_collision_delay, 60 * appkey_collision_count)
                    self._appkey_collision = False
                    self._logger.warning(f"appkey 중복으로 인한 재연결 대기 중 ({delay}초)... (시도 {retry_count + 1}/{max_retries})")
                    if self._streaming_logger:
                        self._streaming_logger.log_appkey_collision(
                            retry_count=retry_count + 1,
                            delay_sec=delay,
                            max_retries=max_retries,
                        )
                else:
                    appkey_collision_count = 0
                    delay = min(max_delay, base_delay * (2 ** retry_count))
                    self._logger.info(f"웹소켓 재연결 대기 중 ({delay}초)... (시도 {retry_count + 1}/{max_retries})")
                await asyncio.sleep(delay)

                retry_count += 1

                if self._streaming_logger:
                    self._streaming_logger.log_reconnect_attempt(
                        attempt_num=retry_count,
                        max_attempts=max_retries,
                        was_collision=was_appkey_collision,
                    )

                if await self._establish_connection():
                    self._logger.info("웹소켓 재연결 성공. 기존 구독 항목을 복구합니다.")
                    if not was_appkey_collision:
                        # appkey 충돌이 아닌 일반 재연결 성공 시에만 카운트 초기화
                        retry_count = 0
                    try:
                        await self._resubscribe_all()
                    except Exception as e:
                        self._logger.error(f"구독 복구 중 오류 발생: {e}")
                continue

            # 2. 메시지 수신
            try:
                # [수정] 타임아웃 적용: 일정 시간 데이터가 없으면 Dead Connection으로 간주하고 재연결
                message = await asyncio.wait_for(self.ws.recv(), timeout=DATA_TIMEOUT)
                self._handle_websocket_message(message)
            except asyncio.TimeoutError:
                # 장 운영 시간 중에만 재연결 시도 (장 마감 후에는 데이터가 없는 것이 정상이므로 무시)
                market_is_open = True
                if self._mcs:
                    market_is_open = await self._mcs.is_market_open_now()
                if market_is_open:
                    self._logger.warning(f"{DATA_TIMEOUT}초간 데이터 수신 없음 (Dead Connection 의심). 재연결을 시도합니다.")
                    self._is_connected = False
                    if self.ws:
                        await self.ws.close()
                    self.ws = None
                    self.approval_key = None  # 재연결 시 새로운 접속키 발급 강제
                # 장 마감 후에는 타임아웃 발생해도 연결 유지 (Ping/Pong은 내부적으로 처리됨)
            except Exception as e:
                if self._auto_reconnect:
                    self._logger.warning(f"웹소켓 연결 끊김 ({e}). 재연결을 시도합니다.", exc_info=True)
                if self._streaming_logger:
                    self._streaming_logger.log_connection_lost(
                        reason=str(e),
                        retry_count=retry_count,
                    )
                self._is_connected = False
                # 서버가 세션을 빨리 해제할 수 있도록 close frame 전송 시도
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                self.ws = None
                self.approval_key = None  # 재연결 시 새로운 접속키 발급 강제

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
            elif tr_id == self._env.active_config['tr_ids']['websocket'].get('unified_realtime_price', 'H0UNCNT0'):  # H0UNCNT0 (KRX+NXT 통합 체결)
                parsed_data = self._parse_stock_contract_data(data_body)  # H0STCNT0와 동일 포맷
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
                        self._logger.exception(f"체결통보 복호화 실패: {tr_id}, 데이터: {data_body[:50]}...")
                        return
                else:
                    self._logger.warning(f"체결통보 암호화 해제 실패: AES 키/IV 없음. TR_ID: {tr_id}, 메시지: {message[:50]}...")
                    return

            elif tr_id in self._get_program_trading_tr_ids():
                parsed_data = self._parse_program_trading_data(data_body)
                message_type = 'realtime_program_trading'

            # [추가] 파싱된 데이터 디버그 로그 (데이터 내용 확인용)
            self._logger.debug(f"WS 수신 데이터 파싱: Type={message_type}, TR_ID={tr_id}, Data={parsed_data}")

            # 외부 콜백 함수로 파싱된 데이터 전달
            if self.on_realtime_message_callback:
                try:
                    self.on_realtime_message_callback({'type': message_type, 'tr_id': tr_id, 'data': parsed_data})
                except Exception as exc:
                    self._logger.error(f"실시간 콜백 처리 중 오류: {exc}", exc_info=True)

        else:  # 제어 메시지 (응답, PINGPONG 등)
            try:
                json_object = _loads(message)
                header = json_object.get("header", {})
                tr_id = header.get("tr_id")

                if tr_id == "PINGPONG":
                    self._logger.debug("PINGPONG 수신됨. PONG 응답.")
                    # websockets 라이브러리 내부에서 PONG 응답 자동 처리 (ping_interval, ping_timeout 설정 시)
                elif json_object.get("body", {}).get("rt_cd") == '0':
                    tr_key = header.get('tr_key')
                    pending = self._pending_requests.pop((tr_id, tr_key), None)
                    tr_type = pending.get("tr_type") if pending else header.get("tr_type")
                    self._logger.info(f"실시간 요청 응답 성공: TR_KEY={tr_key}, MSG={json_object['body']['msg1']}")
                    if tr_type == "1":
                        self._subscribed_items.add((tr_id, tr_key))
                    # 체결통보용 AES KEY, IV 수신 처리
                    if tr_id in ["H0IFCNI0", "H0STCNI0", "H0STCNI9", "H0MFCNI0", "H0EUCNI0"] and json_object.get("body",
                                                                                                                 {}).get(
                            "output"):
                        self._aes_key = json_object["body"]["output"].get("key")
                        self._aes_iv = json_object["body"]["output"].get("iv")
                        self._logger.info(f"체결통보용 AES KEY/IV 수신 성공. TRID={tr_id}")
                else:
                    tr_key = header.get('tr_key')
                    pending = self._pending_requests.pop((tr_id, tr_key), None)
                    tr_type = pending.get("tr_type") if pending else header.get("tr_type")
                    msg1 = json_object.get("body", {}).get("msg1", "")
                    self._logger.error(
                        f"실시간 요청 응답 오류: TR_KEY={tr_key}, RT_CD={json_object.get('body', {}).get('rt_cd')}, MSG={msg1}")
                    if msg1 == 'ALREADY IN SUBSCRIBE':
                        self._logger.warning("이미 구독 중인 종목입니다.")
                        self._subscribed_items.add((tr_id, tr_key))
                    elif 'ALREADY IN USE' in msg1:
                        self._logger.warning("서버가 appkey 중복 사용을 거부했습니다. 재연결 시 대기 시간을 늘립니다.")
                        self._appkey_collision = True
                    elif self._streaming_logger:
                        if tr_type == "2":
                            self._streaming_logger.log_unsubscribe_failure(tr_key or tr_id, msg1 or "ACK error")
                        else:
                            self._streaming_logger.log_subscribe_failure(tr_key or tr_id, msg1 or "ACK error")
            except json.JSONDecodeError:
                self._logger.exception(f"제어 메시지 JSON 디코딩 실패: {message}")
            except Exception as e:
                self._logger.exception(f"제어 메시지 처리 중 오류 발생: {e}, 메시지: {message}")

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

    def _parse_signing_notice(self, data_str: str, tr_id: str) -> dict:
        """H0STCNI0/H0STCNI9 (국내주식 체결통보)를 파싱합니다."""
        values = data_str.split('^')
        if len(values) < 16:
            self._logger.warning(
                f"체결통보 필드 수 부족: TR_ID={tr_id}, field_count={len(values)}, payload={data_str[:100]}"
            )
            return {
                "tr_id": tr_id,
                "통보유형": "unknown",
                "parse_error": "insufficient_fields",
                "field_count": len(values),
                "raw": data_str,
            }

        cntg_yn = values[13]
        is_fill_notice = cntg_yn == "2"
        if is_fill_notice:
            menulist = "고객ID|계좌번호|주문번호|원주문번호|매도매수구분|정정구분|주문종류|주문조건|주식단축종목코드|체결수량|체결단가|주식체결시간|거부여부|체결여부|접수여부|지점번호|주문수량|계좌명|호가조건가격|주문거래소구분|실시간체결창표시여부|필러|신용구분|신용대출일자|체결종목명40|주문가격"
        else:
            menulist = "고객ID|계좌번호|주문번호|원주문번호|매도매수구분|정정구분|주문종류|주문조건|주식단축종목코드|주문수량|주문가격|주식체결시간|거부여부|체결여부|접수여부|지점번호|주문수량_미출력|계좌명|호가조건가격|주문거래소구분|실시간체결창표시여부|필러|신용구분|신용대출일자|체결종목명40|체결단가"
        keys = menulist.split('|')
        parsed = dict(zip(keys, values[:len(keys)]))
        parsed["tr_id"] = tr_id
        parsed["CNTG_YN"] = parsed.get("체결여부", "")
        parsed["ACPT_YN"] = parsed.get("접수여부", "")
        parsed["RFUS_YN"] = parsed.get("거부여부", "")
        if len(values) < len(keys):
            parsed["parse_warning"] = "short_payload"
            parsed["field_count"] = len(values)
            parsed["raw"] = data_str
            self._logger.warning(
                f"체결통보 payload가 공식 필드 수보다 짧습니다: TR_ID={tr_id}, "
                f"field_count={len(values)}, expected={len(keys)}"
            )
        if parsed["RFUS_YN"].upper() == "Y":
            parsed["통보유형"] = "거부"
        elif parsed["CNTG_YN"] == "2":
            parsed["통보유형"] = "체결"
        elif parsed["ACPT_YN"].upper() == "Y":
            parsed["통보유형"] = "접수"
        else:
            parsed["통보유형"] = "unknown"
        return parsed

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

    def _parse_program_trading_data(self, data_str: str) -> dict:
        """H0STPGM0 (국내주식 실시간 프로그램매매) 데이터를 파싱합니다."""
        menulist = "유가증권단축종목코드|주식체결시간|매도체결량|매도거래대금|매수2체결량|매수2거래대금|순매수체결량|순매수거래대금|매도호가잔량|매수호가잔량|전체순매수호가잔량"
        keys = menulist.split('|')
        values = data_str.split('^')
        return dict(zip(keys, values[:len(keys)]))

    def _get_program_trading_tr_ids(self) -> set[str]:
        websocket_config = self._env.active_config.get('tr_ids', {}).get('websocket', {})
        return {
            websocket_config.get('realtime_program_trading', 'H0STPGM0'),
            websocket_config.get('nxt_realtime_program_trading', 'H0NXPGM0'),
        }

    async def subscribe_program_trading(self, stock_code: str):
        """국내주식 실시간 프로그램매매 동향 (H0STPGM0) 구독."""
        tr_id = self._env.active_config['tr_ids']['websocket'].get('realtime_program_trading', 'H0STPGM0')
        self._logger.info(f"[프로그램매매] 종목 {stock_code} 구독 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="1")

    async def unsubscribe_program_trading(self, stock_code: str):
        """국내주식 실시간 프로그램매매 동향 (H0STPGM0) 구독 해지."""
        tr_id = self._env.active_config['tr_ids']['websocket'].get('realtime_program_trading', 'H0STPGM0')
        self._logger.info(f"[프로그램매매] 종목 {stock_code} 구독 해지 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="2")

    async def _resubscribe_all(self):
        """재연결 시 기존 구독 항목들을 다시 구독 요청합니다."""
        items = list(self._subscribed_items)
        for i, (tr_id, tr_key) in enumerate(items):
            self._logger.info(f"구독 복구 요청: TR_ID={tr_id}, KEY={tr_key}")
            # send_realtime_request 내부에서 _subscribed_items에 다시 추가하므로 중복 방지 로직 필요 없음 (Set이므로)
            await self.send_realtime_request(tr_id, tr_key, tr_type="1")
            # 서버 부하 분산을 위해 구독 요청 사이에 짧은 딜레이 삽입
            if i < len(items) - 1:
                await asyncio.sleep(0.1)

    def is_receive_alive(self) -> bool:
        """수신 태스크가 살아있는지 확인 (외부 워치독용)."""
        return (self._receive_task is not None
                and not self._receive_task.done()
                and self._auto_reconnect)

    # --- 웹소켓 연결 및 해지 ---
    async def connect(self, on_message_callback=None):
        """웹소켓 연결을 시작하고 실시간 데이터 수신을 준비합니다."""
        self._websocket_url = self._env.get_websocket_url()
        if self.ws and self._is_connected:
            self._logger.info("웹소켓이 이미 연결되어 있습니다.")
            return True

        self.on_realtime_message_callback = on_message_callback  # 외부 콜백 등록
        self._auto_reconnect = True  # 자동 재연결 활성화

        if await self._establish_connection():
            # 메시지 수신 태스크 시작 (이미 실행 중이면 건너뜀)
            if self._receive_task and not self._receive_task.done():
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass
            self._receive_task = asyncio.create_task(self._receive_messages())
            return True
        return False

    async def disconnect(self):
        """웹소켓 연결을 종료합니다."""
        self._auto_reconnect = False  # 수동 종료 시 재연결 비활성화
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
                    self._logger.error(f"웹소켓 수신 태스크 종료 중 오류: {e}", exc_info=True)
                    
            self._logger.info("웹소켓 연결 종료 완료.")
            self._is_connected = False
            self.ws = None
        else:
            self._logger.info("웹소켓이 연결되어 있지 않습니다.")
            if self._receive_task:
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    self._logger.info("웹소켓 수신 태스크 취소됨.")
                except Exception as e:
                    self._logger.error(f"웹소켓 수신 태스크 종료 중 오류: {e}")
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
            if self._streaming_logger:
                if tr_type == "2":
                    self._streaming_logger.log_unsubscribe_failure(tr_key, "WebSocket 미연결")
                else:
                    self._streaming_logger.log_subscribe_failure(tr_key, "WebSocket 미연결")
            return False
        if not self.approval_key:
            self._logger.error("approval_key가 없어 실시간 요청을 보낼 수 없습니다.")
            if self._streaming_logger:
                if tr_type == "2":
                    self._streaming_logger.log_unsubscribe_failure(tr_key, "approval_key 없음")
                else:
                    self._streaming_logger.log_subscribe_failure(tr_key, "approval_key 없음")
            return False

        header = {
            "approval_key": self.approval_key,
            "custtype": self._env.active_config['custtype'],
            "tr_type": tr_type,
            "content-type": "utf-8",
        }
        body = {
            "input": {
                "tr_id": tr_id,
                "tr_key": tr_key,
            }
        }

        request_message = {"header": header, "body": body}
        message_json = _dumps(request_message)

        self._logger.info(f"실시간 요청 전송: TR_ID={tr_id}, TR_KEY={tr_key}, TYPE={tr_type}")
        try:
            await self.ws.send(message_json)
            self._pending_requests[(tr_id, tr_key)] = {"tr_type": tr_type}
            if tr_type == "2":
                self._subscribed_items.discard((tr_id, tr_key))
            return True
        except Exception as e:
            self._logger.exception(f"실시간 요청 전송 중 오류 발생: {e}")
            self._pending_requests.pop((tr_id, tr_key), None)
            if self._streaming_logger:
                message = f"실시간 요청 전송 예외: {e}"
                if tr_type == "2":
                    self._streaming_logger.log_unsubscribe_failure(tr_key, message)
                else:
                    self._streaming_logger.log_subscribe_failure(tr_key, message)
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

    async def subscribe_unified_price(self, stock_code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0)를 구독합니다. KRX+NXT 통합."""
        tr_id = self._env.active_config['tr_ids']['websocket'].get('unified_realtime_price', 'H0UNCNT0')
        self._logger.info(f"종목 {stock_code} 통합 체결가 구독 요청 ({tr_id})...")
        return await self.send_realtime_request(tr_id, stock_code, tr_type="1")

    async def unsubscribe_unified_price(self, stock_code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독을 해지합니다."""
        tr_id = self._env.active_config['tr_ids']['websocket'].get('unified_realtime_price', 'H0UNCNT0')
        self._logger.info(f"종목 {stock_code} 통합 체결가 구독 해지 ({tr_id})...")
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

    def _get_order_notice_tr_id(self) -> str:
        websocket_config = self._env.active_config.get('tr_ids', {}).get('websocket', {})
        if self._env.is_paper_trading:
            return websocket_config.get('order_notice_paper', 'H0STCNI9')
        return websocket_config.get('order_notice_real', 'H0STCNI0')

    async def subscribe_order_notice(self):
        """국내주식 체결통보를 구독합니다. tr_key는 HTS ID를 사용합니다."""
        tr_id = self._get_order_notice_tr_id()
        htsid = self._env.active_config.get('htsid')
        if not htsid:
            message = "체결통보 구독 실패: htsid가 설정되어 있지 않습니다."
            if not self._env.is_paper_trading:
                self._logger.critical(f"{message} 실전투자 체결통보 채널을 시작할 수 없습니다.")
                raise RuntimeError(message)
            self._logger.error(message)
            return False
        self._logger.info(f"국내주식 체결통보 구독 요청 ({tr_id}, HTS_ID={htsid})...")
        return await self.send_realtime_request(tr_id, htsid, tr_type="1")

    async def unsubscribe_order_notice(self):
        """국내주식 체결통보 구독을 해지합니다."""
        tr_id = self._get_order_notice_tr_id()
        htsid = self._env.active_config.get('htsid')
        if not htsid:
            self._logger.error("체결통보 구독 해지 실패: htsid가 설정되어 있지 않습니다.")
            return False
        self._logger.info(f"국내주식 체결통보 구독 해지 요청 ({tr_id}, HTS_ID={htsid})...")
        return await self.send_realtime_request(tr_id, htsid, tr_type="2")

    # For test only
    async def _on_receive(self, message):
        try:
            parsed = _loads(message)  # <-- JSON 문자열을 dict로 파싱
            if self.on_realtime_message_callback:
                await self.on_realtime_message_callback(parsed)
            else:
                self._logger.warning("수신된 메시지를 처리할 콜백이 등록되지 않았습니다.")
        except Exception as e:
            self._logger.exception(f"수신 메시지 처리 중 예외 발생: {e}")
