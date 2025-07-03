import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_token_manager import TokenManager
import requests
import logging
import httpx # 에러 시뮬레이션을 위해 import


@pytest.mark.asyncio
async def test_log_request_exception_cases(caplog):
    mock_token_manager = MagicMock()
    api = KoreaInvestApiBase("http://test", {}, {},mock_token_manager, logger=None)

    class DummyResponse:
        status_code = 500
        text = "error"
    http_error = requests.exceptions.HTTPError(response=DummyResponse())
    connection_error = requests.exceptions.ConnectionError("conn")
    timeout_error = requests.exceptions.Timeout("timeout")
    request_exception = requests.exceptions.RequestException("req")
    json_error = json.JSONDecodeError("msg", "doc", 0)
    generic_exception = Exception("generic")

    with caplog.at_level("ERROR"):
        for exc in [http_error, connection_error, timeout_error, request_exception, json_error, generic_exception]:
            api._log_request_exception(exc)

    for expected in ["HTTP ", " ", "", " ", "JSON ", " "]:
        assert any(expected in message for message in caplog.messages)

@pytest.mark.asyncio
async def test_execute_request_post(monkeypatch):
    mock_token_manager = MagicMock()
    api = KoreaInvestApiBase("http://test", {}, {},mock_token_manager, logger=None)
    mock_response = MagicMock()

    mock_response.status_code = 200
    monkeypatch.setattr(api._session, "post", lambda *a, **k: mock_response)
    result = await api._execute_request("POST", "http://test", {}, {"x": "y"})
    assert result.status_code == 200

@pytest.mark.asyncio
async def test_execute_request_invalid_method():
    mock_token_manager = MagicMock()

    api = KoreaInvestApiBase("http://test", {}, {},mock_token_manager, logger=None)
    api._session = MagicMock()

    with pytest.raises(ValueError):
        await api._execute_request("PUT", "http://test", {}, {})

class ExplodingString(str):
    def encode(self, encoding='utf-8', errors='strict'):
        raise UnicodeEncodeError(encoding, self, 0, 1, "intentional failure")

class ExplodingHeader:
    def __str__(self):
        return ExplodingString("trigger")

@pytest.mark.asyncio
async def test_log_headers_unicode_error_with_custom_object(caplog):
    # 테스트에 사용할 헤더
    exploding_headers = {"bad": ExplodingHeader()}
    mock_token_manager = MagicMock()
    mock_config = MagicMock()

    # --- 수정된 부분 ---
    # headers 인자에 위에서 생성한 exploding_headers를 정확히 전달합니다.
    # 불필요한 빈 딕셔너리 {}를 제거합니다.
    api = KoreaInvestApiBase(
        base_url="http://test",
        headers=exploding_headers,
        config=mock_config,  # 'config' 인자를 전달
        token_manager=mock_token_manager,
        logger=None
    )
    # --------------------

    with caplog.at_level("DEBUG"):
        api._log_headers()

    # 이제 로그에 에러 메시지가 정상적으로 포함됩니다.
    assert "*** UnicodeEncodeError ***" in caplog.text


@pytest.mark.asyncio
async def test_call_api_with_http_error_status(caplog):
    # --- Arrange (준비) ---
    # 1. HTTP 500 오류를 내는 가짜 응답 객체 생성
    # _handle_response 내부 로직과 호환되도록 httpx.Response 스펙을 따릅니다.
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    # _handle_response의 raise_for_status()가 호출될 때 실제 에러를 발생시키도록 설정
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Server Error", request=MagicMock(), response=mock_response
    )

    # 2. 의존성 객체(config, token_manager)를 가짜로 생성
    mock_config = MagicMock()
    mock_token_manager = MagicMock(spec=TokenManager)

    # 3. 테스트 대상 API 인스턴스 생성
    api = KoreaInvestApiBase(
        base_url="http://test",
        headers={},
        config=mock_config,
        token_manager=mock_token_manager
    )

    # ▼▼▼ 핵심 수정 부분 ▼▼▼
    # 실제 네트워크 호출을 하는 _execute_request를 패치하고,
    # 미리 만들어둔 가짜 응답(mock_response)을 반환하도록 설정합니다.
    with patch.object(api, '_execute_request', new_callable=AsyncMock, return_value=mock_response) as mock_execute_request:
        # --- Act (실행) ---
        # 이제 call_api는 실제 네트워크 통신 없이 즉시 mock_response를 받게 됩니다.
        result = await api.call_api("GET", "/fail")

        # --- Assert (검증) ---
        # _execute_request가 1번 호출되었는지 확인
        mock_execute_request.assert_awaited_once()

        # _handle_response 로직에 의해 최종적으로 None이 반환되어야 함
        assert result is None

        # _handle_response가 남기는 로그가 정상적으로 찍혔는지 확인
        assert "HTTP 오류 발생: 500 - Internal Server Error" in caplog.text

        # 재시도 루프가 돌지 않았으므로 '모든 재시도 실패' 로그는 없어야 함
        assert "모든 재시도 실패" not in caplog.text

@pytest.mark.asyncio
async def test_call_api_with_invalid_json_type(caplog):
    """응답이 dict가 아님"""
    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.json.return_value = "not a dict"
    mock_token_manager = MagicMock()

    api = KoreaInvestApiBase("http://test", {}, {},mock_token_manager, logger=None)
    api._session.request = MagicMock(return_value=response_mock)

    result = await api.call_api("GET", "/invalid")
    assert result is None
    assert any("예상치 못한 예외 발생" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_call_api_token_renew_failed(caplog):
    """토큰 재발급 후에도 실패"""
    mock_env = MagicMock()
    mock_env.refresh_access_token.return_value = False

    config = {"_env_instance": mock_env}
    mock_token_manager = MagicMock()
    api = KoreaInvestApiBase("http://test", {}, {},mock_token_manager, logger=None)
    api._env = mock_env  # config와 별도로 보장

    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.text = "expired"
    response_mock.json.return_value = {
        "rt_cd": "1",
        "msg_cd": "EGW00123"
    }

    api._session.request = MagicMock(return_value=response_mock)

    with caplog.at_level(logging.ERROR):
        result = await api.call_api("GET", "/token-expired")

    assert result is None
    assert "토큰" in caplog.text and "실패" in caplog.text


@pytest.mark.asyncio
async def test_call_api_no_env_instance(caplog):
    from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
    logger_name = KoreaInvestApiBase.__module__

    # logger 직접 설정
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True  # caplog 연결

    # 주입
    mock_token_manager = MagicMock()
    api = KoreaInvestApiBase("http://test", {}, {},mock_token_manager, logger=None)
    api._env = None

    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.text = "expired"
    response_mock.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00123"}
    api._session.request = MagicMock(return_value=response_mock)

    with caplog.at_level(logging.ERROR, logger=logger_name):
        result = await api.call_api("GET", "/no-env")

    print("\n=== Captured Log ===")
    for r in caplog.records:
        print(f"[{r.levelname}] {r.name} - {r.message}")
    print("=====================\n")

    assert result is None
    assert any("토큰 만료 오류" in r.message for r in caplog.records)

