import aiohttp
import logging
from typing import Optional, List, Dict
from managers.notification_manager import NotificationEvent

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Telegram 알림을 비동기적으로 전송하는 핸들러 클래스입니다."""

    def __init__(self, bot_token: str, chat_id: str, allowed_categories: Optional[List[str]] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        # 허용할 카테고리 목록 설정 (None이면 모든 카테고리 허용)
        self.allowed_categories = allowed_categories

    async def handle_event(self, event: NotificationEvent) -> None:
        """NotificationManager에서 호출할 비동기 콜백 메서드입니다."""
        # ★ 필터링 로직: 허용된 카테고리가 설정되어 있고, 현재 이벤트 카테고리가 거기에 없으면 무시
        if self.allowed_categories is not None and event.category not in self.allowed_categories:
            return
        
        # 특정 레벨(예: info, warning, error, critical)에 따라 이모지나 포맷을 다르게 할 수 있습니다.
        level_emoji = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨"
        }.get(event.level.lower(), "🔔")

        # 텔레그램으로 보낼 메시지 포맷 구성
        text = (
            f"{level_emoji} <b>[{event.category}] {event.title}</b>\n"
            f"시간: {event.timestamp}\n"
            f"내용:\n{event.message}"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        # 비동기 HTTP 요청으로 Telegram API 호출
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        logger.error(f"Telegram 알림 전송 실패: {response.status} - {response_text}")
        except Exception as e:
            logger.error(f"Telegram 알림 전송 중 예외 발생: {e}")


class TelegramReporter:
    """텔레그램으로 정형화된 리포트(랭킹 등)를 전송하는 클래스입니다."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    async def _send_message(self, text: str) -> bool:
        """텔레그램으로 메시지를 비동기적으로 전송하는 헬퍼 메서드입니다."""
        if not text:
            return True

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        logger.error(f"Telegram 리포트 전송 실패: {response.status} - {response_text}")
                        return False
            return True
        except Exception as e:
            logger.error(f"Telegram 리포트 전송 중 예외 발생: {e}")
            return False

    def _format_ranking_table(self, title: str, ranking_data: List[Dict], value_field: str, limit: int = 10) -> str:
        """랭킹 데이터를 HTML 포맷의 문자열로 변환합니다."""
        if not ranking_data:
            return ""

        header = f"<b>🏆 {title} (Top {limit})</b>\n"
        table = "<pre>"
        table += "{:<2} {:<12} {:>10}\n".format("순위", "종목명", "금액(억)")
        table += "-" * 26 + "\n"

        for i, item in enumerate(ranking_data[:limit]):
            rank = i + 1
            name = item.get('hts_kor_isnm', 'N/A')
            # 긴 종목명 자르기 (한글 등 멀티바이트 문자 고려)
            display_name = ""
            display_width = 0
            for char in name:
                char_width = 2 if '\uac00' <= char <= '\ud7a3' else 1
                if display_width + char_width > 9:
                    display_name += "…"
                    break
                display_name += char
                display_width += char_width

            value_str = item.get(value_field, "0")
            try:
                # 금액(원)을 억 단위로 변환
                value = int(value_str or "0") / 100_000_000
                value_formatted = f"{value:,.0f}"
            except (ValueError, TypeError):
                value_formatted = "N/A"

            padding = 12 - display_width
            table += f"{rank:<2} {display_name}{' ' * padding} {value_formatted:>10}\n"

        table += "</pre>"
        return header + table

    async def send_ranking_report(self, rankings: Dict[str, List[Dict]], report_date: str):
        """
        다양한 랭킹 정보를 하나의 리포트로 묶어 텔레그램에 전송합니다.
        rankings 딕셔너리는 'foreign_buy', 'all_stocks' 등의 키를 가집니다.
        """
        report_parts = []

        # 1. 개별 랭킹
        report_parts.append(self._format_ranking_table("외국인 순매수", rankings.get('foreign_buy', []), 'frgn_ntby_tr_pbmn'))
        report_parts.append(self._format_ranking_table("기관 순매수", rankings.get('inst_buy', []), 'orgn_ntby_tr_pbmn'))
        report_parts.append(self._format_ranking_table("프로그램 순매수", rankings.get('program_buy', []), 'whol_smtn_ntby_tr_pbmn'))
        report_parts.append(self._format_ranking_table("외국인 순매도", rankings.get('foreign_sell', []), 'frgn_ntby_tr_pbmn'))
        report_parts.append(self._format_ranking_table("기관 순매도", rankings.get('inst_sell', []), 'orgn_ntby_tr_pbmn'))
        report_parts.append(self._format_ranking_table("프로그램 순매도", rankings.get('program_sell', []), 'whol_smtn_ntby_tr_pbmn'))

        # 2. 조합 랭킹
        all_stocks = rankings.get('all_stocks')
        program_all_stocks = rankings.get('program_all_stocks')

        if all_stocks:
            # 외인+기관
            fi_combined = []
            for stock in all_stocks:
                try:
                    f_net = int(stock.get('frgn_ntby_tr_pbmn', '0') or '0')
                    i_net = int(stock.get('orgn_ntby_tr_pbmn', '0') or '0')
                    new_stock = stock.copy()
                    new_stock['fi_combined_net'] = f_net + i_net
                    fi_combined.append(new_stock)
                except (ValueError, TypeError): continue

            report_parts.append(self._format_ranking_table("외인+기관 순매수", sorted(fi_combined, key=lambda x: x['fi_combined_net'], reverse=True), 'fi_combined_net'))
            report_parts.append(self._format_ranking_table("외인+기관 순매도", sorted(fi_combined, key=lambda x: x['fi_combined_net']), 'fi_combined_net'))

            # 외인+기관+프로그램
            if program_all_stocks:
                prog_map = {p['stck_shrn_iscd']: int(p.get('whol_smtn_ntby_tr_pbmn', '0') or '0') for p in program_all_stocks}
                fip_combined = []
                for stock in all_stocks:
                    try:
                        f_net = int(stock.get('frgn_ntby_tr_pbmn', '0') or '0')
                        i_net = int(stock.get('orgn_ntby_tr_pbmn', '0') or '0')
                        p_net = prog_map.get(stock.get('stck_shrn_iscd'), 0)
                        new_stock = stock.copy()
                        new_stock['fip_combined_net'] = f_net + i_net + p_net
                        fip_combined.append(new_stock)
                    except (ValueError, TypeError): continue

                report_parts.append(self._format_ranking_table("외인+기관+프로그램 순매수", sorted(fip_combined, key=lambda x: x['fip_combined_net'], reverse=True), 'fip_combined_net'))
                report_parts.append(self._format_ranking_table("외인+기관+프로그램 순매도", sorted(fip_combined, key=lambda x: x['fip_combined_net']), 'fip_combined_net'))

        # 3. 거래대금
        report_parts.append(self._format_ranking_table("거래대금 상위", rankings.get('trading_value', []), 'acml_tr_pbmn'))

        # 메시지 분할 전송
        title = f"🔔 <b>장 마감 랭킹 리포트 ({report_date})</b>\n"
        await self._send_message(title)

        current_message = ""
        for part in filter(None, report_parts):
            if len(current_message.encode('utf-8')) + len(part.encode('utf-8')) + 2 > 4096:
                await self._send_message(current_message)
                current_message = part
            else:
                current_message += ("\n\n" + part) if current_message else part

        if current_message:
            await self._send_message(current_message)