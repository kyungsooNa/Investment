# services/notification_service.py
import asyncio
from typing import List, Dict, Any, Protocol

class NotificationHandler(Protocol):
    """알림 핸들러 인터페이스 (프로토콜)."""
    async def send(self, event: Dict[str, Any]):
        ...

class WebUIHandler:
    """웹 UI (SSE) 클라이언트로 알림을 전송하는 핸들러."""
    def __init__(self):
        self._queues: List[asyncio.Queue] = []

    async def add_client(self, queue: asyncio.Queue):
        """새 웹 클라이언트 큐를 등록합니다."""
        self._queues.append(queue)

    def remove_client(self, queue: asyncio.Queue):
        """웹 클라이언트 큐를 제거합니다."""
        if queue in self._queues:
            self._queues.remove(queue)

    async def send(self, event: Dict[str, Any]):
        """모든 웹 클라이언트에게 알림을 보냅니다."""
        for queue in self._queues:
            # 큐가 가득 찼을 경우를 대비한 비동기 처리
            try:
                await asyncio.wait_for(queue.put(event), timeout=1.0)
            except asyncio.TimeoutError:
                print(f"Warning: A web client queue was full. Notification lost.")


class NotificationService:
    """
    알림을 다양한 채널로 전송하는 중앙 서비스.
    Observer 패턴을 사용하여 여러 핸들러(Web, Email 등)를 등록하고 관리합니다.
    """
    _instance = None
    _handlers: List[NotificationHandler] = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(NotificationService, cls).__new__(cls)
        return cls._instance

    def register_handler(self, handler: NotificationHandler):
        """새 알림 핸들러를 등록합니다."""
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unregister_handler(self, handler: NotificationHandler):
        """알림 핸들러를 제거합니다."""
        if handler in self._handlers:
            self._handlers.remove(handler)
            
    def get_handler(self, handler_type: type) -> NotificationHandler | None:
        """특정 타입의 핸들러를 찾아서 반환합니다."""
        for handler in self._handlers:
            if isinstance(handler, handler_type):
                return handler
        return None

    async def send_notification(self, message: str, type: str = 'info'):
        """모든 등록된 핸들러에게 알림을 보냅니다."""
        event = {"message": message, "type": type}
        # 여러 핸들러를 동시에 비동기적으로 실행
        await asyncio.gather(*(handler.send(event) for handler in self._handlers))

# --- 싱글톤 인스턴스 및 기본 핸들러 설정 ---
notification_service = NotificationService()
web_ui_handler = WebUIHandler()
notification_service.register_handler(web_ui_handler)

