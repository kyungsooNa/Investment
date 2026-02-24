import pytest
from unittest.mock import MagicMock

# 페이지별 라우트와 예상되는 active_page 값 매핑
PAGES = [
    ("/", "stock"),
    ("/balance", "balance"),
    ("/order", "order"),
    ("/ranking", "ranking"),
    ("/marketcap", "marketcap"),
    ("/virtual", "virtual"),
    ("/scheduler", "scheduler"),
    ("/program", "program"),
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
        if path == "/":
            assert 'href="/" class="active"' in response.text
        else:
            assert f'href="{path}" class="active"' in response.text
        
        # 각 페이지별 특징적인 요소 확인
        if path == "/":
            assert "종목 현재가 조회" in response.text
        elif path == "/balance":
            assert "계좌 잔고" in response.text
        elif path == "/order":
            assert "주식 주문" in response.text
        elif path == "/ranking":
            assert "상위 종목 랭킹" in response.text
        elif path == "/marketcap":
            assert "시가총액 상위 종목" in response.text
        elif path == "/virtual":
            assert "모의투자(전략 검증) 결과" in response.text
        elif path == "/scheduler":
            assert "전략 스케줄러" in response.text
        elif path == "/program":
            assert "프로그램매매 실시간 동향" in response.text

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

def test_pages_render_success_with_login(web_client, mock_web_ctx):
    """로그인 기능 활성화 시 올바른 토큰으로 접근하면 페이지가 렌더링되는지 테스트"""
    # 로그인 활성화 설정
    mock_web_ctx.full_config = {"use_login": True, "auth": {"secret_key": "secret_token"}}
    
    # 올바른 쿠키 설정
    web_client.cookies.set("access_token", "secret_token")
    
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