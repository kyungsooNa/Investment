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
    ("/overseas-stock", "overseas_stock"),
    ("/overseas-favorite", "overseas_favorite"),
    ("/overseas-marketcap", "overseas_marketcap"),
    ("/overseas-ranking", "overseas_ranking"),
    ("/ranking", "ranking"),
    ("/marketcap", "marketcap"),
    ("/virtual", "virtual"),
    ("/scheduler", "scheduler"),
    ("/program", "program"),
    ("/system", "system"),
    ("/heatmap", "heatmap"),
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
            assert 'id="overseas-tab-favorite"' not in response.text
            assert 'id="overseas-panel-marketcap"' in response.text
            assert 'id="overseas-panel-favorite"' in response.text
            assert 'id="overseas-fav-symbol"' in response.text
            assert 'id="overseas-favorite-body"' in response.text
            assert "/static/js/overseas.js" in response.text
        elif path == "/overseas-stock":
            assert "미국 종목 현재가 조회" in response.text
            assert 'id="overseas-stock-symbol"' in response.text
            assert 'id="overseas-stock-exchange"' in response.text
            assert 'id="overseas-stock-result"' in response.text
            assert 'id="stock-chart-card"' in response.text
            assert "/static/js/overseas_stock.js" in response.text
            # 한국장 전용 입력/조회 경로가 섞이면 안 된다.
            assert 'id="stock-code-input"' not in response.text
            assert "/static/js/stock.js" not in response.text
        elif path == "/overseas-favorite":
            assert "미국장 즐겨찾기" in response.text
            assert 'id="overseas-panel-favorite"' in response.text
            assert 'id="overseas-tab-favorite"' not in response.text
            assert 'id="overseas-fav-symbol"' in response.text
            assert 'id="overseas-favorite-body"' in response.text
            assert "/static/js/overseas.js" in response.text
        elif path == "/overseas-marketcap":
            assert "미국 시가총액 상위 종목" in response.text
            assert 'id="overseas-marketcap-result"' in response.text
            assert 'data-limit="100"' in response.text
            assert "/static/js/overseas_marketcap.js" in response.text
        elif path == "/overseas-ranking":
            assert "미국 상위 종목 랭킹" in response.text
            assert 'id="overseas-ranking-result"' in response.text
            for category in ("rise", "fall", "volume", "trading_value"):
                assert f'data-category="{category}"' in response.text
            # 외국인/기관/개인 수급은 KRX 전용이라 미국장 화면에 두지 않는다.
            assert 'data-category="foreign_buy"' not in response.text
            assert "/static/js/overseas_ranking.js" in response.text
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
        elif path == "/heatmap":
            assert "시장 히트맵" in response.text
            assert 'id="heatmap-page-viewport"' in response.text
            assert "/static/js/heatmap_page.js" in response.text


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


def test_virtual_static_js_marks_suspect_records():
    """오염 의심 플래그가 붙은 매도 기록은 수익률 옆에 표식이 보여야 한다.

    소급 재구성이 불가한 오염 기록(PR #700 이전 부분매도 전량기록)은 숫자를 고치지
    않고 표식만 남긴다 — 표식이 사라지면 오염 수치가 정상 성과로 읽힌다.
    """
    script = Path("view/web/static/js/virtual.js").read_text(encoding="utf-8")

    assert "data_quality_flag" in script
    assert "suspectHtml" in script


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

def test_overseas_stock_static_js_exposes_quote_workflow():
    """overseas_stock.js가 미국장 현재가 조회와 일봉 차트를 담당해야 한다."""
    script = Path("view/web/static/js/overseas_stock.js").read_text(encoding="utf-8")

    assert "/api/overseas/stock/" in script
    assert "searchOverseasStock" in script
    assert "loadAndRenderOverseasStockChart" in script
    # 국내 종목 조회 API 를 끌어다 쓰면 한국장 화면과 경로가 뒤섞인다.
    assert "/api/stock/" not in script


def test_pages_are_not_browser_cached(web_client, mock_web_ctx):
    """페이지 HTML 은 캐시 버스팅 수단이 없어(정적 파일만 ?v=), no-store 로 내려야 한다.

    이게 없으면 배포 후에도 브라우저 휴리스틱 캐시 때문에 예전 화면이 남는다
    (실제로 미국장 즐겨찾기 탭이 안 보인다는 보고가 있었다).
    """
    mock_web_ctx.full_config = {"use_login": False}

    for path, _ in PAGES:
        response = web_client.get(path)

        assert response.status_code == 200
        assert "no-store" in response.headers.get("cache-control", ""), (
            f"{path} 응답에 no-store 가 없어 예전 화면이 캐시될 수 있다"
        )


def test_login_page_is_not_browser_cached(web_client, mock_web_ctx):
    """로그인 페이지가 캐시되면 로그인 후에도 로그인 화면이 남을 수 있다."""
    mock_web_ctx.full_config = {"use_login": True, "auth": {"secret_key": "secret_token"}}
    web_client.cookies.clear()

    response = web_client.get("/stock")

    assert response.status_code == 200
    assert "Investment Login" in response.text
    assert "no-store" in response.headers.get("cache-control", "")


def test_forbidden_page_is_not_browser_cached(web_client, mock_web_ctx):
    """권한 부족 응답이 캐시되면 권한 승급 후에도 403 화면이 남을 수 있다."""
    auth_config = {"secret_key": "secret_token", "session_max_age_seconds": 3600}
    mock_web_ctx.full_config = {"use_login": True, "auth": auth_config}
    token, _ = issue_session(auth_config, "reader", role="viewer")
    web_client.cookies.set(SESSION_COOKIE_NAME, token)

    response = web_client.get("/balance")

    assert response.status_code == 403
    assert "no-store" in response.headers.get("cache-control", "")


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


def test_viewer_balance_page_is_forbidden_before_account_lookup(
    web_client,
    mock_web_ctx,
):
    auth_config = {
        "secret_key": "secret_token",
        "session_max_age_seconds": 3600,
    }
    mock_web_ctx.full_config = {"use_login": True, "auth": auth_config}
    token, _ = issue_session(
        auth_config,
        "reader",
        role="viewer",
    )
    web_client.cookies.set(SESSION_COOKIE_NAME, token)

    response = web_client.get("/balance")

    assert response.status_code == 403
    assert "권한이 부족합니다." in response.text
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
