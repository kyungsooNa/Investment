# services/naver_finance_scraper.py
import aiohttp
import logging
from bs4 import BeautifulSoup
from typing import Optional

class NaverFinanceScraper:
    """네이버 금융 웹페이지 스크래핑을 전담하는 서비스 클래스."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._logger = logger or logging.getLogger(__name__)
        # 차단 방지를 위한 User-Agent 설정
        self._headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    async def fetch_yoy_profit_growth(self, code: str) -> float:
        """
        특정 종목의 최근 분기(또는 예상치)와 전년 동기 영업이익을 비교하여 YoY 성장률을 반환합니다.
        가장 최근 분기(예상치 등) 데이터가 비어있을 경우, 직전 분기 실적으로 후퇴(Fallback)하여 계산합니다.
        턴어라운드(적자 -> 흑자 전환) 시그널 발생 시 999.0을 반환합니다.
        """
        url = f"https://finance.naver.com/item/main.naver?code={code}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers, timeout=5) as response:
                    if response.status != 200:
                        self._logger.warning({"event": "scraping_http_error", "code": code, "status": response.status})
                        return 0.0
                    html = await response.text()

            soup = BeautifulSoup(html, 'html.parser')

            div_cop = soup.find('div', class_='cop_analysis')
            if not div_cop: return 0.0

            tbody = div_cop.find('tbody')
            if not tbody: return 0.0

            op_row = None
            for tr in tbody.find_all('tr'):
                th = tr.find('th')
                if th and '영업이익' in th.text:
                    op_row = tr
                    break

            if not op_row: return 0.0

            tds = op_row.find_all('td')
            quarterly_tds = tds[4:] # 앞 4열은 연간, 뒤 6열은 최근 분기
            
            # 최소한 후퇴(Fallback) 로직을 처리할 수 있을 만큼의 열이 있는지 확인
            if len(quarterly_tds) < 6: return 0.0

            # 1. 우선 가장 최근 분기(-1)와 전년 동기(-5) 데이터 추출 시도
            latest_str = quarterly_tds[-1].text.replace(',', '').strip()
            yoy_str = quarterly_tds[-5].text.replace(',', '').strip()

            # 2. 대안(Fallback) 탐색: 최신 데이터가 비어있거나 '-'인 경우, 직전 확정 분기(-2)로 후퇴
            if not latest_str or latest_str == '-':
                latest_str = quarterly_tds[-2].text.replace(',', '').strip()
                yoy_str = quarterly_tds[-6].text.replace(',', '').strip()
                self._logger.debug({"event": "fallback_to_previous_quarter", "code": code})

            # 3. 후퇴 후에도 데이터가 유효하지 않으면 0.0 반환
            if not latest_str or latest_str == '-' or not yoy_str or yoy_str == '-':
                return 0.0

            latest_op = float(latest_str)
            yoy_op = float(yoy_str)

            # --- 턴어라운드 (적자 -> 흑자) 예외 처리 ---
            if yoy_op <= 0 and latest_op > 0:
                self._logger.debug({"event": "turnaround_detected", "code": code, "yoy": yoy_op, "latest": latest_op})
                return 999.0

            # --- 일반 YoY 성장률 계산 ---
            if yoy_op > 0 and latest_op > 0:
                return ((latest_op - yoy_op) / yoy_op) * 100.0

            return 0.0

        except Exception as e:
            self._logger.warning({"event": "scraping_failed", "code": code, "error": str(e)})
            return 0.0