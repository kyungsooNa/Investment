# tests/services/test_naver_finance_scraper.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from services.naver_finance_scraper import NaverFinanceScraper

# ─── Mock HTML 생성 헬퍼 함수 ────────────────────────────────────
def generate_mock_html(td_values: list) -> str:
    """
    네이버 금융의 '기업실적분석' 표 중 '영업이익' 행을 흉내내는 HTML을 생성합니다.
    """
    tds_html = "".join([f"<td>{val}</td>" for val in td_values])
    return f"""
    <div class="cop_analysis">
        <table>
            <tbody>
                <tr>
                    <th scope="row">영업이익</th>
                    {tds_html}
                </tr>
            </tbody>
        </table>
    </div>
    """

@pytest.fixture
def scraper():
    # 로거 출력을 무시하기 위해 더미 로거 주입 (원하면 놔둬도 무방)
    return NaverFinanceScraper(logger=MagicMock())

# ─── 테스트 케이스 정의 ───────────────────────────────────────────
@pytest.mark.asyncio
class TestNaverFinanceScraper:

    @pytest.mark.parametrize(
        "test_name, td_values, expected_growth",
        [
            (
                "정상 성장 (100 -> 150 = 50%)",
                # 앞 4개(연간) + 뒤 6개(분기). 분기: [-6]...[-5]...[-2][-1]
                # [-5]가 전년동기(100), [-1]이 최근분기(150)
                ["연간1", "연간2", "연간3", "연간4", "분기1", "100", "분기3", "분기4", "분기5", "150"],
                50.0
            ),
            (
                "턴어라운드 (적자 -50 -> 흑자 100 = 999.0)",
                ["연간1", "연간2", "연간3", "연간4", "분기1", "-50", "분기3", "분기4", "분기5", "100"],
                999.0
            ),
            (
                "Fallback 정상 성장 (예상치 비어있음, 직전 분기 100 -> 150 = 50%)",
                # [-1]이 '-', [-2]가 150(최근확정), [-6]이 100(전년동기)
                ["연간1", "연간2", "연간3", "연간4", "100", "분기2", "분기3", "분기4", "150", "-"],
                50.0
            ),
            (
                "Fallback 턴어라운드 (예상치 비어있음, 적자 -20 -> 흑자 100)",
                ["연간1", "연간2", "연간3", "연간4", "-20", "분기2", "분기3", "분기4", "100", ""],
                999.0
            ),
            (
                "적자 지속 (-20 -> -10 = 0.0)",
                ["연간1", "연간2", "연간3", "연간4", "분기1", "-20", "분기3", "분기4", "분기5", "-10"],
                0.0
            ),
            (
                "Fallback시에도 데이터 부족 (둘 다 비어있음 = 0.0)",
                ["연간1", "연간2", "연간3", "연간4", "100", "분기2", "분기3", "분기4", "-", "-"],
                0.0
            ),
        ]
    )
    @patch("aiohttp.ClientSession.get")
    async def test_fetch_yoy_profit_growth_scenarios(self, mock_get, scraper, test_name, td_values, expected_growth):
        # 1. Mock 설정: aiohttp 응답 가로채기
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text.return_value = generate_mock_html(td_values)
        
        # async context manager (__aenter__, __aexit__) 모킹
        mock_get.return_value.__aenter__.return_value = mock_response

        # 2. 메서드 실행
        result = await scraper.fetch_yoy_profit_growth("000000")

        # 3. 결과 검증
        assert result == expected_growth, f"실패한 케이스: {test_name} (기대값: {expected_growth}, 결과값: {result})"

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_yoy_profit_growth_http_error(self, mock_get, scraper):
        """HTTP 상태 코드가 200이 아닐 경우 0.0을 반환하는지 테스트"""
        mock_response = AsyncMock()
        mock_response.status = 404 # 에러 상태 코드
        mock_get.return_value.__aenter__.return_value = mock_response

        result = await scraper.fetch_yoy_profit_growth("000000")
        assert result == 0.0

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_yoy_profit_growth_missing_table(self, mock_get, scraper):
        """웹페이지 구조가 변경되어 표를 찾을 수 없을 때 0.0을 반환하는지 테스트"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text.return_value = "<html><body>표가 없습니다</body></html>"
        mock_get.return_value.__aenter__.return_value = mock_response

        result = await scraper.fetch_yoy_profit_growth("000000")
        assert result == 0.0

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_yoy_profit_growth_connection_error(self, mock_get, scraper):
        """네트워크 연결 실패, 타임아웃 등 웹 접근 자체에 예외가 발생했을 때 0.0을 반환하는지 테스트"""
        
        # mock_get이 호출될 때 정상 응답 대신 aiohttp의 연결 에러를 강제로 발생시킴 (side_effect 활용)
        import aiohttp
        mock_get.side_effect = aiohttp.ClientConnectionError("서버에 연결할 수 없습니다.")

        result = await scraper.fetch_yoy_profit_growth("000000")
        
        # 예외를 잘 먹고(catch) 0.0을 반환하는지 검증
        assert result == 0.0