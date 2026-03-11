import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from managers.telegram_notifier import TelegramNotifier, TelegramReporter
from managers.notification_manager import NotificationEvent

@pytest.fixture
def telegram_notifier():
    """TelegramNotifier 인스턴스 픽스처"""
    return TelegramNotifier(bot_token="test_bot_token", chat_id="test_chat_id")

@pytest.fixture
def sample_event():
    """테스트용 알림 이벤트 픽스처"""
    return NotificationEvent(
        id="test_id_123",
        timestamp="2026-03-11T10:00:00",
        category="TRADE",
        level="critical",
        title="매수 시그널",
        message="삼성전자 72,000원 매수 체결",
        metadata={}
    )

def test_init(telegram_notifier):
    """초기화 및 API URL 생성 테스트"""
    assert telegram_notifier.bot_token == "test_bot_token"
    assert telegram_notifier.chat_id == "test_chat_id"
    assert telegram_notifier.api_url == "https://api.telegram.org/bottest_bot_token/sendMessage"

@pytest.mark.asyncio
async def test_handle_event_success(telegram_notifier, sample_event):
    """텔레그램 메시지 전송 성공 테스트 (200 OK)"""
    # aiohttp.ClientSession.post 호출을 모킹
    with patch("aiohttp.ClientSession.post") as mock_post:
        # 비동기 컨텍스트 매니저 (async with) 반환값 설정
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response

        # 이벤트 처리 실행
        await telegram_notifier.handle_event(sample_event)

        # post가 1번 호출되었는지 검증
        mock_post.assert_called_once()
        
        # 호출될 때 전달된 인자(URL과 payload) 검증
        call_args, call_kwargs = mock_post.call_args
        assert call_args[0] == telegram_notifier.api_url
        
        payload = call_kwargs.get("json")
        assert payload is not None
        assert payload["chat_id"] == "test_chat_id"
        assert payload["parse_mode"] == "HTML"
        
        # 메시지 텍스트 포맷팅 검증 ('critical' 레벨이므로 🚨 이모지 포함 예상)
        text = payload["text"]
        assert "🚨" in text
        assert "[TRADE] 매수 시그널" in text
        assert "삼성전자 72,000원 매수 체결" in text

@pytest.mark.asyncio
async def test_handle_event_api_error(telegram_notifier, sample_event, caplog):
    """텔레그램 API 응답 실패 테스트 (예: 400 Bad Request)"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text.return_value = "Bad Request"
        mock_post.return_value.__aenter__.return_value = mock_response

        # 실행
        await telegram_notifier.handle_event(sample_event)

        # 시스템 중단 없이 예외가 로깅되었는지 확인
        assert "Telegram 알림 전송 실패: 400 - Bad Request" in caplog.text

@pytest.mark.asyncio
async def test_handle_event_exception(telegram_notifier, sample_event, caplog):
    """네트워크 연결 오류 등 예외 발생 테스트"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        # post 호출 시도 자체에서 예외 발생하도록 설정
        mock_post.return_value.__aenter__.side_effect = Exception("Connection Timeout")

        # 실행
        await telegram_notifier.handle_event(sample_event)

        # 애플리케이션이 뻗지 않고 예외가 안전하게 잡혀 로그로 남았는지 확인
        assert "Telegram 알림 전송 중 예외 발생: Connection Timeout" in caplog.text

@pytest.fixture
def filter_notifier():
    """TRADE 카테고리만 허용하는 TelegramNotifier 인스턴스 픽스처"""
    return TelegramNotifier(
        bot_token="test_bot_token", 
        chat_id="test_chat_id", 
        allowed_categories=["TRADE"]
    )

@pytest.mark.asyncio
async def test_handle_event_filtered_out(filter_notifier):
    """허용되지 않은 카테고리(SYSTEM) 이벤트가 들어왔을 때 API 호출을 하지 않는지 테스트"""
    system_event = NotificationEvent(
        id="test_id_999",
        timestamp="2026-03-11T10:00:00",
        category="SYSTEM",  # TRADE가 아님
        level="info",
        title="시스템 시작",
        message="시스템이 성공적으로 시작되었습니다.",
    )

    with patch("aiohttp.ClientSession.post") as mock_post:
        # 이벤트 처리 실행
        await filter_notifier.handle_event(system_event)

        # SYSTEM 카테고리는 무시되어야 하므로 post 메서드가 단 한 번도 호출되지 않아야 함
        mock_post.assert_not_called()


# --- TelegramReporter Tests ---

@pytest.fixture
def telegram_reporter():
    return TelegramReporter(bot_token="test_token", chat_id="test_chat_id")

@pytest.mark.asyncio
async def test_reporter_send_message(telegram_reporter):
    """TelegramReporter._send_message 메서드 동작 검증"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await telegram_reporter._send_message("테스트 메시지")
        
        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]['json']
        assert payload['chat_id'] == "test_chat_id"
        assert payload['text'] == "테스트 메시지"

def test_format_ranking_table(telegram_reporter):
    """랭킹 테이블 포맷팅 검증"""
    data = [
        {'hts_kor_isnm': '삼성전자', 'value': '10000000000'}, # 100억
        {'hts_kor_isnm': 'SK하이닉스', 'value': '5000000000'}   # 50억
    ]
    
    table = telegram_reporter._format_ranking_table("테스트 랭킹", data, "value")
    
    assert "🏆 테스트 랭킹" in table
    assert "<pre>" in table
    assert "삼성전자" in table
    assert "100" in table # 100억
    assert "50" in table  # 50억

@pytest.mark.asyncio
async def test_send_ranking_report_splits_message(telegram_reporter):
    """리포트가 너무 길 경우 메시지를 분할해서 전송하는지 검증"""
    # 매우 긴 데이터 생성 (4096 바이트 초과 유도)
    long_data = [{'hts_kor_isnm': f'종목{i}', 'val': '100'} for i in range(100)]
    
    rankings = {
        'foreign_buy': long_data,
        'inst_buy': long_data,
        'program_buy': long_data,
        # ...
    }
    
    # _send_message를 Mocking하여 호출 횟수 카운트
    telegram_reporter._send_message = AsyncMock(return_value=True)
    
    # 테이블 포맷팅이 긴 문자열을 반환하도록 설정 (실제 로직 사용)
    
    await telegram_reporter.send_ranking_report(rankings, "20250101")
    
    # 최소 2번 이상 메시지 전송이 호출되어야 함 (타이틀 1번 + 내용 분할 N번)
    assert telegram_reporter._send_message.call_count >= 2
    
    # 첫 메시지는 타이틀이어야 함
    first_call_text = telegram_reporter._send_message.call_args_list[0][0][0]
    assert "장 마감 랭킹 리포트" in first_call_text