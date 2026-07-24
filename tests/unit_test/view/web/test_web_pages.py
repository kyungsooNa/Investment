import pytest
from pathlib import Path
from unittest.mock import MagicMock
from view.web.security import SESSION_COOKIE_NAME, issue_session

# 페이지별 라우트와 예상되는 active_page 값 매핑
PAGES = [
    ("/", "home"),
    ("/stock", "stock"),
    ("/balance", "balance"),
    ("/order", "order"),
    ("/overseas", "overseas"),
    ("/ranking", "ranking"),
    ("/marketcap", "marketcap"),
    ("/virtual", "virtual"),
    ("/scheduler", "scheduler"),
    ("/program", "program"),
    ("/system", "system"),
]

def test_pages_render_success_no_login(web_client, mock_web_ctx):
    """로그인 기능이 비활성화된 경우 모든 페이지가 정상 렌더링되는지 테스트"""
    # 로그인 비활성화 설정
    mock_web_ctx.full_config = {"use_login": False}
    
    for path, _ in PAGES:
        response = web_client.get(path)
        
        assert response.status_code == 200
        # base.html을 상속받으므로 공통 타이틀 확인
        assert "<title>Investment - Web View</title>" in response.text
        
        # 네비게이션 바 활성화 상태 확인
        assert f'href="{path}" class="active"' in response.text

        # 각 페이지별 특징적인 요소 확인
        if path == "/":
            assert "Investment" in response.text
        elif path == "/stock":
            assert "종목 현재가 조회" in response.text
            assert 'id="stock-market-label"' in response.text
            assert 'id="stock-code-input"' in response.text
            assert 'id="stock-mode-overseas"' not in response.text
            assert 'id="overseas-stock-row"' not in response.text
        elif path == "/balance":
            assert "계좌 잔고" in response.text
        elif path == "/order":
            assert "주식 주문" in response.text
        elif path == "/overseas":
            assert "미국주식" in response.text
            assert 'id="overseas-symbol"' in response.text
            assert 'id="overseas-tab-overview"' in response.text
            assert 'id="overseas-tab-marketcap"' in response.text
            assert 'id="overseas-tab-orders"' in response.text
            assert 'id="overseas-panel-marketcap"' in response.text
            assert "/static/js/overseas.js" in response.text
        elif path == "/ranking":
            assert "상위 종목 랭킹" in response.text
            assert 'data-cat="ytd"' in response.text
            assert "YTD 상승률" in response.text
        elif path == "/marketcap":
            assert "시가총액 상위 종목" in response.text
        elif path == "/virtual":
            assert "모의투자(전략 검증) 결과" in response.text
            assert 'id="apply-cost-chk" onchange="loadVirtualHistory()" checked' in response.text
            assert 'id="virtual-divergence-summary"' in response.text
            assert 'id="virtual-backtest-run-select"' in response.text
            assert 'id="virtual-backtest-journal-input"' in response.text
            assert 'id="virtual-divergence-body"' in response.text
            assert "<th>진입</th>" in response.text
            assert "<th>Stage</th>" in response.text
            assert "<th>체결강도</th>" in response.text
            assert "<th>주문</th>" in response.text
            assert "<th>체결수량</th>" in response.text
            assert "<th>슬리피지</th>" in response.text
        elif path == "/scheduler":
            assert "전략 스케줄러" in response.text
        elif path == "/program":
            assert "프로그램매매 실시간 동향" in response.text
        elif path == "/system":
            assert "시스템 상태 모니터링" in response.text


def test_virtual_static_js_exposes_divergence_workflow():
    """virtual.js가 표준 journal / 괴리 비교 API를 호출할 수 있어야 한다."""
    script = Path("view/web/static/js/virtual.js").read_text(encoding="utf-8")

    assert "/api/virtual/journal" in script
    assert "/api/virtual/backtest-journals" in script
    assert "/api/virtual/backtest-divergence" in script
    assert "loadVirtualBacktestJournalRuns" in script
    assert "compareVirtualDivergence" in script
    assert "filled_qty" in script
    assert "slippage_pct" in script


def test_stock_static_js_does_not_expose_overseas_mode():
    """stock.js는 한국장 전용이며 미국장 조회는 overseas.js가 소유한다."""
    script = Path("view/web/static/js/stock.js").read_text(encoding="utf-8")

    assert "setStockMarketMode" not in script
    assert "searchOverseasStock" not in script
    assert "/api/overseas/" not in script


def test_overseas_static_js_exposes_manual_workflow():
    """overseas.js가 해외 조회/잔고/수동 주문 API를 호출할 수 있어야 한다."""
    script = Path("view/web/static/js/overseas.js").read_text(encoding="utf-8")

    assert "/api/overseas/stock/" in script
    assert "/api/overseas/balance" in script
    assert "/api/overseas/order" in script
    assert "loadOverseasQuote" in script
    assert "placeOverseasOrder" in script

def test_pages_show_login_page_when_unauthorized(web_client, mock_web_ctx):
    """로그인 기능 활성화 시 토큰 없이 접근하면 로그인 페이지가 렌더링되는지 테스트"""
    # 로그인 활성화 설정
    mock_web_ctx.full_config = {"use_login": True, "auth": {"secret_key": "secret_token"}}
    
    for path, _ in PAGES:
        # 쿠키 없이 요청
        web_client.cookies.clear()
        response = web_client.get(path)
        
        assert response.status_code == 200
        # 로그인 페이지 특징 확인
        assert "Investment Login" in response.text


def test_balance_page_authenticates_before_account_lookup(web_client, mock_web_ctx):
    """비인증 balance 페이지 요청은 broker 계좌 조회를 시작하지 않는다."""
    mock_web_ctx.full_config = {"use_login": True, "auth": {"secret_key": "secret_token"}}
    web_client.cookies.clear()

    response = web_client.get("/balance")

    assert response.status_code == 200
    assert "Investment Login" in response.text
    mock_web_ctx.stock_query_service.handle_get_account_balance.assert_not_awaited()


def test_pages_render_success_with_login(web_client, mock_web_ctx):
    """로그인 기능 활성화 시 올바른 토큰으로 접근하면 페이지가 렌더링되는지 테스트"""
    # 로그인 활성화 설정
    auth_config = {"secret_key": "secret_token", "session_max_age_seconds": 3600}
    mock_web_ctx.full_config = {"use_login": True, "auth": auth_config}

    # 올바른 쿠키 설정
    token, _ = issue_session(auth_config, "test-operator")
    web_client.cookies.set(SESSION_COOKIE_NAME, token)
    
    for path, _ in PAGES:
        response = web_client.get(path)
        
        assert response.status_code == 200
        # 로그인 페이지가 아님을 확인
        assert "Investment Login" not in response.text
        assert "<title>Investment - Web View</title>" in response.text

def test_logout(web_client):
    """로그아웃 기능 테스트"""
    response = web_client.get("/logout", follow_redirects=False)
    
    # 리다이렉트 확인
    assert response.status_code == 307
    assert response.headers["location"] == "/"
    
    # 쿠키 삭제 확인 (Set-Cookie 헤더 확인)
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    # 만료 날짜가 과거이거나 Max-Age가 0인지 확인하여 삭제 여부 검증
    assert 'Max-Age=0' in set_cookie or 'Expires=' in set_cookie
