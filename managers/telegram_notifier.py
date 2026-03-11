import aiohttp
import logging
from typing import Optional, List
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