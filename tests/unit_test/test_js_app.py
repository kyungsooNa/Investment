import pytest
import os

# app.js 파일 경로 (프로젝트 구조에 맞게 설정)
APP_JS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../view/web/static/js/app.js"))

@pytest.fixture(scope="function")
def js_page(page):
    """
    Playwright 페이지에 app.js를 로드하고 필요한 DOM 및 Mock을 설정하는 Fixture.
    이 테스트를 실행하려면 'pip install pytest-playwright' 및 'playwright install'이 필요합니다.
    """
    if not os.path.exists(APP_JS_PATH):
        pytest.fail(f"app.js not found at {APP_JS_PATH}")

    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        js_content = f.read()

    # 1. 테스트에 필요한 최소한의 DOM 구조 정의
    # 2. fetch 및 setInterval 등 사이드 이펙트 함수 Mocking
    # 3. app.js 코드 주입
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <body>
        <!-- app.js가 참조하는 DOM 요소들 -->
        <div id="toast-container"></div>
        <span id="status-time"></span>
        <span id="status-market"></span>
        <span id="status-env"></span>
        <div id="section-virtual"></div>
        
        <script>
            // [Mock] fetch API: 네트워크 요청 차단
            window.fetch = async () => ({{
                ok: true,
                json: async () => ({{}}),
                text: async () => ""
            }});

            // [Mock] setInterval: 백그라운드 작업 차단
            window.setInterval = () => {{}};
            
            // app.js 내용 주입
            {js_content}
        </script>
    </body>
    </html>
    """
    
    page.set_content(html_content)
    return page

def test_format_trading_value(js_page):
    """[JS] formatTradingValue 함수 로직 검증"""
    print("\n[DEBUG] test_format_trading_value: JS 함수 테스트 시작")
    
    # 1. 1억 이상 (소수점 1자리 확인: toFixed(1))
    # 1.5억 -> 1.5억
    assert js_page.evaluate("formatTradingValue(150000000)") == "1.5억"
    # 1.4억 -> 1.4억
    assert js_page.evaluate("formatTradingValue(140000000)") == "1.4억"
    
    # 2. 1만 이상 1억 미만
    assert js_page.evaluate("formatTradingValue(50000)") == "5만"
    assert js_page.evaluate("formatTradingValue(99999)") == "10만"
    
    # 3. 1만 미만 (콤마 포맷팅)
    assert js_page.evaluate("formatTradingValue(1234)") == "1,234"
    
    # 4. 0 또는 빈 값
    assert js_page.evaluate("formatTradingValue(0)") == "0"
    assert js_page.evaluate("formatTradingValue('')") == "0"
    
    # 5. 음수 처리
    assert js_page.evaluate("formatTradingValue(-150000000)") == "-2억"

def test_format_market_cap(js_page):
    """[JS] formatMarketCap 함수 로직 검증"""
    print("\n[DEBUG] test_format_market_cap: JS 함수 테스트 시작")
    # 입력 단위는 '억원'임
    
    # 1. 10조 이상 (반올림)
    # 105000억원 -> 10.5조 -> 11조
    assert js_page.evaluate("formatMarketCap(105000)") == "11조"
    
    # 2. 1조 이상 10조 미만 (소수점 1자리)
    # 15000억원 -> 1.5조
    assert js_page.evaluate("formatMarketCap(15000)") == "1.5조"
    
    # 3. 1조 미만
    assert js_page.evaluate("formatMarketCap(5000)") == "5,000억"

def test_ensure_table_in_card(js_page):
    """[JS] ensureTableInCard DOM 조작 검증"""
    print("\n[DEBUG] test_ensure_table_in_card: DOM 조작 테스트 시작")
    
    # 초기 상태: 테이블만 있고 카드는 없음
    js_page.evaluate("""() => {
        const div = document.createElement('div');
        div.id = 'test-container';
        div.innerHTML = '<table id="test-table"></table>';
        document.body.appendChild(div);
    }""")
    
    # 함수 실행
    js_page.evaluate("ensureTableInCard(document.getElementById('test-table'))")
    
    # 검증: 테이블의 부모가 .card 클래스를 가진 div여야 함
    is_wrapped = js_page.evaluate("""() => {
        const table = document.getElementById('test-table');
        return table.parentElement.classList.contains('card');
    }""")
    
    assert is_wrapped is True