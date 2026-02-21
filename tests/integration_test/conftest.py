#tests/integration_test/conftest.py

import os
import stat
import shutil
import pytest
import logging
import json
from core.cache.cache_manager import CacheManager
from core.cache.cache_wrapper import ClientWithCache
from core.logger import Logger  # ⬅️ 추가
from unittest.mock import MagicMock, AsyncMock
from typing import Any, Dict, Iterable, Optional
from tests.integration_test import ctx  # ← 방금 만든 모듈


@pytest.fixture(autouse=True)
def patch_cache_wrap_client_for_tests(mocker):
    # 캐시를 적용하지 않고 원본 클라이언트를 그대로 반환하여 충돌 방지
    def bypass_cache(client, logger, time_manager, env_fn, config=None):
        return client

    mocker.patch("brokers.broker_api_wrapper.cache_wrap_client", side_effect=bypass_cache)

@pytest.fixture(scope="session")
def test_cache_config():
    test_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".cache"))
    return {
        "cache": {
            "base_dir": test_base_dir,
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }


@pytest.fixture(scope="function")
def cache_manager(test_cache_config):
    return CacheManager(config=test_cache_config)


@pytest.fixture(autouse=True)
def clear_cache_files(test_cache_config):
    base_dir = test_cache_config["cache"]["base_dir"]

    def on_rm_error(func, path, _):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception as e:
            print(f"❌ 파일 삭제 실패: {path} - {e}")

    # ✅ 캐시 디렉토리 삭제 전 log 핸들 닫기
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

    yield

    # ✅ 캐시 디렉토리 삭제 후에도 log 핸들 정리
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

@pytest.fixture(scope="function")
def test_logger(request):
    # 📌 현재 conftest.py 기준 ./log 경로 생성
    log_dir = os.path.join(os.path.dirname(__file__), "log")
    logger = Logger(log_dir=log_dir)

    # 실행되는 테스트 케이스 이름 로깅
    tc_name = request.node.name
    logger.operational_logger.info(f"===== [TEST START] {tc_name} =====")
    logger.debug_logger.debug(f"===== [TEST START] {tc_name} =====")

    # MagicMock으로 감싸 호출 검증도 가능하게
    logger_proxy = MagicMock(wraps=logger)
    yield logger_proxy

    # 종료 로그 남기기
    logger_proxy.operational_logger.info(f"===== [TEST END] {tc_name} =====")
    logger_proxy.debug_logger.debug(f"===== [TEST END] {tc_name} =====")

    # 핸들러 정리 (윈도우 잠금 방지)
    for lg in (logger.operational_logger, logger.debug_logger):
        for h in lg.handlers[:]:
            try:
                h.close()
            finally:
                lg.removeHandler(h)

# ---- HTTP 응답 빌더 ---------------------------------------------------------
def make_http_response(payload: Dict[str, Any], status: int = 200, headers: Optional[Dict[str, str]] = None):
    """
    _handle_response 가 기대하는 속성(status_code/json/text/content/headers)을 가진
    가짜 HTTP 응답 객체를 만들어 반환합니다.
    """
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = json.dumps(payload, ensure_ascii=False)
    resp.content = resp.text.encode("utf-8")
    resp.json = lambda: payload
    return resp

# ---- 숫자 변환/데이터 추출 유틸 ---------------------------------------------
def to_int(val: Any) -> Optional[int]:
    try:
        return int(str(val).replace(",", ""))
    except Exception:
        return None

def extract_src_from_balance_payload(p: Dict[str, Any]) -> Dict[str, Any]:
    """
    계좌 잔고 payload에서 실제 금액 필드를 담은 dict를 반환합니다.
    - 표준 스키마(output) 우선
    - 없으면 output1[0]
    """
    if isinstance(p, dict) and "output" in p and isinstance(p["output"], dict):
        return p["output"]
    out1 = p.get("output1") or []
    return out1[0] if out1 else {}

# ---- Client 언랩/엔드포인트 URL 유틸 ----------------------------------------
def _unwrap_client(app) -> Any:
    """
    BrokerAPIWrapper._client 이 ClientWithCache 래퍼일 수 있으므로
    실제 KoreaInvestApiClient 까지 언랩해서 반환.
    """
    client = app.stock_query_service.trading_service._broker_api_wrapper._client
    if hasattr(client, "_client"):
        client = client._client
    return client

def _get_quotations_api(app) -> Any:
    return _unwrap_client(app)._quotations  # KoreaInvestApiQuotations

def _get_account_api(app) -> Any:
    """
    계좌/트레이딩 API 인스턴스를 찾아 반환.
    내부 구조가 프로젝트마다 조금 다를 수 있어 방어적으로 탐색.
    """
    client = _unwrap_client(app)
    for name in ("_account", "_trading", "_accounts"):
        if hasattr(client, name):
            api = getattr(client, name)
            if hasattr(api, "url") and hasattr(api, "_async_session"):
                return api
    raise AssertionError("Account API instance not found on client.")

# ---- 유틸: trading API 탐색 -------------------------------------------------
def _get_trading_api(app):
    """
    주문에 사용되는 trading 계열 API 인스턴스를 탐색해서 반환.
    - 후보 속성명을 여러 개 시도
    - url()과 _async_session 보유 여부로 필터
    """
    client = _unwrap_client(app)
    candidates = (
        "_trading", "_trade", "_orders", "_order", "_trader", "_trading_api"
    )
    for name in candidates:
        if hasattr(client, name):
            api = getattr(client, name)
            if hasattr(api, "url") and hasattr(api, "_async_session"):
                return api
    # 마지막 안전망: client의 public 속성들 중 조건 맞는 첫 번째
    for name in dir(client):
        if name.startswith("_"):
            continue
        api = getattr(client, name)
        if hasattr(api, "url") and hasattr(api, "_async_session"):
            # 클래스명에 trading/order 힌트가 있으면 가산점
            cls = type(api).__name__.lower()
            if "trad" in cls or "order" in cls:
                return api
    return None

def expected_url_for_quotations(app, key) -> str:
    return _get_quotations_api(app).url(key)

def expected_url_for_account(app, key) -> str:
    return _get_account_api(app).url(key)

# ---- 세션 모킹 헬퍼 ---------------------------------------------------------
def patch_session_get(api, mocker, payload: Dict[str, Any], status: int = 200):
    """
    _execute_request는 실제로 실행되도록 두고, 네트워크 레이어만 차단.
    api._async_session.get 을 AsyncMock 으로 패치하고 가짜 응답을 반환.
    """
    return mocker.patch.object(
        api._async_session,
        "get",
        new_callable=AsyncMock,
        return_value=make_http_response(payload, status),
    )

def patch_session_post(api, mocker, payload: Dict[str, Any], status: int = 200):
    """
    api._async_session.post 를 AsyncMock 으로 패치하고 가짜 응답을 반환.
    (주의: 구현은 json= 이 아니라 data= 로 전송하는지 확인 필요)
    """
    return mocker.patch.object(
        api._async_session,
        "post",
        new_callable=AsyncMock,
        return_value=make_http_response(payload, status),
    )

# ---- 스파이 헬퍼 ------------------------------------------------------------
def spy_execute_request(api, mocker):
    """
    인스턴스 스파이: _execute_request 를 실제 실행시키되, 호출 인자는 추적 가능.
    (첫 두 args = method, url)
    """
    return mocker.spy(api, "_execute_request")

# ---- 공통 픽스처 ------------------------------------------------------------
def resolve_trid(provider, leaf, kind: str = "trading"):
    """
    provider의 메서드 네이밍이 프로젝트마다 다른 것을 감안하여
    kind(=trading/account/quotations)에 맞는 우선순위로 호출 가능한 메서드를 찾아 TRID를 반환.
    """
    if provider is None:
        raise AssertionError("TRID provider is None")

    PREFERRED = {
        "trading": ["trading"],
        "account": ["account"],
        "quotations": ["quotations"],
    }
    # kind 우선 → 다른 후보 메서드로 폴백
    candidates = PREFERRED.get(kind, []) + ["account", "quotations", "trading", "trade", "orders", "order"]
    for name in candidates:
        fn = getattr(provider, name, None)
        if callable(fn):
            return fn(leaf)

    raise AssertionError(f"No suitable TRID resolver on provider for kind={kind}")


@pytest.fixture
def ki_providers():
    """
    테스트 내에서 provider/env 정보를 편하게 꺼낼 수 있도록 하는 래퍼.
    사용: prov = ki_providers(); prov.client, prov.env, prov.trid_quotations ...
    """
    class _Prov:
        def bind(self, app):
            client = _unwrap_client(app)
            self.client = client
            self.env = getattr(client, "_env", None)

            # 시세
            self.quot = getattr(client, "_quotations", None)
            self.trid_quotations = getattr(self.quot, "_trid_provider", None) if self.quot else None

            # 계좌
            self.account_api = None
            try:
                self.account_api = _get_account_api(app)
            except AssertionError:
                pass
            self.trid_account = getattr(self.account_api, "_trid_provider", None) if self.account_api else None

            # 주문/트레이딩 ✅ 추가
            self.trading_api = _get_trading_api(app)
            self.trid_trading = getattr(self.trading_api, "_trid_provider", None) if self.trading_api else None

            return self
    return _Prov()

@pytest.fixture
def spy_exec_and_patch_get():
    """
    (api, mocker, payload, status=200) -> (spy_exec, mock_get)
    - _execute_request 인스턴스 스파이
    - _async_session.get 패치
    간편 콤보 헬퍼
    """
    def _inner(api, mocker, payload: Dict[str, Any], status: int = 200):
        spy_exec = spy_execute_request(api, mocker)
        mock_get = patch_session_get(api, mocker, payload, status)
        return spy_exec, mock_get
    return _inner

@pytest.fixture
def spy_exec_and_patch_post():
    """
    (api, mocker, payload, status=200) -> (spy_exec, mock_post)
    - _execute_request 인스턴스 스파이
    - _async_session.post 패치
    """
    def _inner(api, mocker, payload: Dict[str, Any], status: int = 200):
        spy_exec = spy_execute_request(api, mocker)
        mock_post = patch_session_post(api, mocker, payload, status)
        return spy_exec, mock_post
    return _inner

def patch_post_with_hash_and_order(api, mocker, order_payload, hash_value="abc123", order_key=None):
    """
    하나의 AsyncMock으로 해시키(/uapi/hashkey)와 주문(ORDER_CASH)을 모두 처리.
    - api: trading/account API 인스턴스
    - order_payload: 주문 성공 응답 페이로드(dict)
    - hash_value: 해시키 응답 값
    - order_key: EndpointKey.ORDER_CASH (기본)
    """
    from brokers.korea_investment.korea_invest_url_keys import EndpointKey
    if order_key is None:
        order_key = EndpointKey.ORDER_CASH

    expected_order_url = api.url(order_key)

    async def _side_effect(url, *args, **kwargs):
        u = str(url)
        if "hashkey" in u:  # /uapi/hashkey
            return make_http_response({"HASH": hash_value}, 200)
        if u == expected_order_url:
            return make_http_response(order_payload, 200)
        # 기타 호출이 있어도 성공처럼 넘김(필요시 tighten)
        return make_http_response({"rt_cd": "0", "msg1": "ok"}, 200)

    spy_exec = mocker.spy(api, "_execute_request")
    mock_post = mocker.patch.object(api._async_session, "post", new_callable=AsyncMock)
    mock_post.side_effect = _side_effect
    return spy_exec, mock_post, expected_order_url

@pytest.fixture(autouse=True)
def _inject_test_helpers(ki_providers, spy_exec_and_patch_get, spy_exec_and_patch_post):
    ctx.ki = ki_providers
    ctx.spy_get = spy_exec_and_patch_get
    ctx.spy_post = spy_exec_and_patch_post
    ctx.to_int = to_int
    ctx.resolve_trid = resolve_trid
    ctx.expected_url_for_quotations = expected_url_for_quotations
    ctx.expected_url_for_account = expected_url_for_account
    ctx.extract_src_from_balance_payload = extract_src_from_balance_payload  # ← 추가
    ctx.patch_post_with_hash_and_order = patch_post_with_hash_and_order
    ctx.make_http_response = make_http_response

