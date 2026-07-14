from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx

from repositories.dart_disclosure_repository import DartDisclosureRepository
from repositories.favorite_repository import FavoriteRepository
from services.dart_disclosure_client import DartDisclosureClient
from services.dart_disclosure_rule_service import DartDisclosureRuleService
from services.telegram_notifier import TelegramReporter
from task.background.always_on.dart_disclosure_monitor_task import DartDisclosureMonitorTask


class _Clock:
    def get_current_kst_time(self):
        return datetime(2026, 7, 14, 10, 0, 0)


async def test_it_new_favorite_disclosure_is_sent_once_and_persisted(tmp_path):
    def handler(request: httpx.Request):
        assert request.url.params["bgn_de"] == "20260714"
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "page_no": 1,
                "page_count": 100,
                "total_count": 1,
                "total_page": 1,
                "list": [
                    {
                        "corp_cls": "Y",
                        "corp_name": "삼성전자",
                        "corp_code": "00126380",
                        "stock_code": "005930",
                        "report_nm": "전환사채권발행결정",
                        "rcept_no": "20260714001234",
                        "flr_nm": "삼성전자",
                        "rcept_dt": "20260714",
                        "rm": "유",
                    }
                ],
            },
        )

    favorites = FavoriteRepository(tmp_path / "favorites.db")
    await favorites.add("005930")
    repository = DartDisclosureRepository(tmp_path / "dart.db")
    await repository.mark_initialized()
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    config = SimpleNamespace(
        poll_interval_sec=300,
        off_hours_interval_sec=1800,
        active_start_time="07:00",
        active_end_time="19:30",
        immediate_alert_score=70,
        daily_digest_enabled=True,
        daily_digest_time="19:40",
        max_pages_per_poll=5,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        task = DartDisclosureMonitorTask(
            client=DartDisclosureClient("test-key", http_client=http_client),
            repository=repository,
            favorite_repository=favorites,
            rule_service=DartDisclosureRuleService(),
            telegram_reporter=reporter,
            config=config,
            market_clock=_Clock(),
            logger=MagicMock(),
        )
        await task._tick()
        await task._tick()

    assert reporter._send_message.await_count == 1
    message = reporter._send_message.await_args.args[0]
    assert "전환사채권발행결정" in message
    assert "rcpNo=20260714001234" in message
    assert await repository.get_pending_immediate(70) == []
