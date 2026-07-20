from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx

from repositories.dart_disclosure_repository import DartDisclosureRepository
from repositories.favorite_repository import FavoriteRepository
from services.ai_disclosure_analyzer import AiDisclosureAnalyzer
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


async def test_it_actual_body_promotes_generic_title_to_immediate_alert(tmp_path):
    receipt_no = "20260720800314"

    def handler(request: httpx.Request):
        if request.url.host == "opendart.fss.or.kr":
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
                            "corp_name": "한미반도체",
                            "corp_code": "00161383",
                            "stock_code": "042700",
                            "report_nm": "기업가치제고계획(자율공시)",
                            "rcept_no": receipt_no,
                            "flr_nm": "한미반도체",
                            "rcept_dt": "20260720",
                            "rm": "유",
                        }
                    ],
                },
            )
        if request.url.path == "/dsaf001/main.do":
            return httpx.Response(
                200,
                text=f'viewDoc("{receipt_no}", "11482555", "0", "0", "0", "HTML", "");',
            )
        if request.url.path == "/report/viewer.do":
            return httpx.Response(
                200,
                text=(
                    "<html><body>2026년 말 하이브리드 본더 시제품 출시, "
                    "2027년 상반기 전용 공장 가동, 미국 법인 설립 추진</body></html>"
                ),
            )
        raise AssertionError(f"unexpected request: {request.url}")

    favorites = FavoriteRepository(tmp_path / "favorites.db")
    await favorites.add("042700")
    repository = DartDisclosureRepository(tmp_path / "dart.db")
    await repository.mark_initialized()
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(
        return_value=(
            '{"summary":"신제품·전용 공장·미국 법인을 추진합니다.",'
            '"score":75,"reasons":["구체적인 신제품 및 생산능력 확대 계획"]}'
        )
    )
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
            ai_analyzer=AiDisclosureAnalyzer(ai_client),
        )
        await task._tick()

    stored = await repository.get_recent_by_stock_code("042700")
    assert stored[0].importance.score == 75
    assert reporter._send_message.await_count == 1
    message = reporter._send_message.await_args.args[0]
    assert "신제품·전용 공장·미국 법인" in message
    assert "75점" in message
