import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from services.telegram_notifier import TelegramNotifier, TelegramReporter
from services.notification_service import NotificationEvent, NotificationCategory, NotificationLevel

@pytest.fixture
def telegram_notifier():
    """TelegramNotifier 인스턴스 픽스처"""
    return TelegramNotifier(
        strategy_bot_token="test_strategy_bot_token", 
        backlog_bot_token="test_backlog_bot_token", 
        chat_id="test_chat_id"
    )

@pytest.fixture
def sample_event():
    """테스트용 알림 이벤트 픽스처"""
    return NotificationEvent(
        id="test_id_123",
        timestamp="2026-03-11T10:00:00",
        category=NotificationCategory.STRATEGY,
        level=NotificationLevel.CRITICAL,
        title="매수 시그널",
        message="삼성전자 72,000원 매수 체결",
        metadata={}
    )

@pytest.fixture
def background_event():
    """백그라운드 카테고리용 알림 이벤트 픽스처"""
    return NotificationEvent(
        id="test_id_456",
        timestamp="2026-03-11T10:05:00",
        category=NotificationCategory.BACKGROUND,
        level=NotificationLevel.INFO,
        title="백그라운드 작업 완료",
        message="데이터 수집이 완료되었습니다.",
        metadata={}
    )

@pytest.fixture
def system_event():
    """시스템 카테고리용 알림 이벤트 픽스처"""
    return NotificationEvent(
        id="test_id_789",
        timestamp="2026-03-11T10:10:00",
        category=NotificationCategory.SYSTEM,
        level=NotificationLevel.ERROR,
        title="시스템 오류",
        message="데이터베이스 연결 실패",
        metadata={}
    )

@pytest.fixture
def api_event():
    """API 카테고리용 알림 이벤트 픽스처"""
    return NotificationEvent(
        id="test_id_999",
        timestamp="2026-03-11T10:15:00",
        category=NotificationCategory.API,
        level=NotificationLevel.WARNING,
        title="API 응답 지연",
        message="API 호출이 지연되고 있습니다.",
        metadata={}
    )

def test_init(telegram_notifier):
    """초기화 및 API URL 생성 테스트"""
    assert telegram_notifier.strategy_bot_token == "test_strategy_bot_token"
    assert telegram_notifier.backlog_bot_token == "test_backlog_bot_token"
    assert telegram_notifier.chat_id == "test_chat_id"
    assert telegram_notifier.strategy_api_url == "https://api.telegram.org/bottest_strategy_bot_token/sendMessage"
    assert telegram_notifier.backlog_api_url == "https://api.telegram.org/bottest_backlog_bot_token/sendMessage"

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
        assert call_args[0] == telegram_notifier.strategy_api_url
        
        payload = call_kwargs.get("json")
        assert payload is not None
        assert payload["chat_id"] == "test_chat_id"
        assert payload["parse_mode"] == "HTML"
        
        # 메시지 텍스트 포맷팅 검증 ('critical' 레벨이므로 🚨 이모지 포함 예상)
        text = payload["text"]
        assert "🚨" in text
        assert "[STRATEGY] 매수 시그널" in text
        assert "삼성전자 72,000원 매수 체결" in text

@pytest.mark.asyncio
async def test_handle_event_backlog_bot(telegram_notifier, background_event):
    """BACKGROUND 카테고리 이벤트가 backlog_bot_token API URL로 전송되는지 테스트"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response

        # 백그라운드 이벤트 처리 실행
        await telegram_notifier.handle_event(background_event)

        mock_post.assert_called_once()
        
        # 호출될 때 전달된 URL 검증 (backlog_api_url이어야 함)
        call_args, call_kwargs = mock_post.call_args
        assert call_args[0] == telegram_notifier.backlog_api_url
        
        payload = call_kwargs.get("json")
        assert payload is not None
        assert payload["chat_id"] == "test_chat_id"
        assert "[BACKGROUND] 백그라운드 작업 완료" in payload["text"]
        assert "데이터 수집이 완료되었습니다." in payload["text"]

@pytest.mark.asyncio
async def test_handle_event_system_bot(telegram_notifier, system_event):
    """SYSTEM 카테고리 이벤트가 backlog_bot_token API URL로 전송되는지 테스트"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response

        # 시스템 이벤트 처리 실행
        await telegram_notifier.handle_event(system_event)

        mock_post.assert_called_once()
        
        # 호출될 때 전달된 URL 검증 (backlog_api_url이어야 함)
        call_args, call_kwargs = mock_post.call_args
        assert call_args[0] == telegram_notifier.backlog_api_url
        
        payload = call_kwargs.get("json")
        assert payload is not None
        assert payload["chat_id"] == "test_chat_id"
        assert "[SYSTEM] 시스템 오류" in payload["text"]
        assert "데이터베이스 연결 실패" in payload["text"]

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
    """STRATEGY 카테고리만 허용하는 TelegramNotifier 인스턴스 픽스처"""
    notifier = TelegramNotifier(
        strategy_bot_token="test_strategy_bot_token",
        backlog_bot_token="test_backlog_bot_token",
        chat_id="test_chat_id"
    )
    notifier.allowed_categories = [NotificationCategory.STRATEGY]
    return notifier

@pytest.mark.asyncio
async def test_handle_event_filtered_out(filter_notifier, api_event):
    """허용되지 않은 카테고리(API) 이벤트가 들어왔을 때 API 호출을 하지 않는지 테스트"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        # 이벤트 처리 실행
        await filter_notifier.handle_event(api_event)

        # API 카테고리는 무시되어야 하므로 post 메서드가 단 한 번도 호출되지 않아야 함
        mock_post.assert_not_called()

@pytest.mark.asyncio
async def test_handle_event_return_rate_positive(telegram_notifier):
    """수익률이 양수일 때 이모지와 텍스트 변환(사유 포함) 검증"""
    event = NotificationEvent(
        id="1", timestamp="2026", category=NotificationCategory.STRATEGY, level=NotificationLevel.INFO, title="매도",
        message="테스트\n사유: 조건 만족", metadata={"return_rate": 5.5}
    )
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response
        await telegram_notifier.handle_event(event)
        payload = mock_post.call_args[1]["json"]
        assert "📈 수익: +5.50%\n사유:" in payload["text"]

@pytest.mark.asyncio
async def test_handle_event_return_rate_negative_no_reason(telegram_notifier):
    """수익률이 음수이고 '사유:' 텍스트가 없을 때 검증"""
    event = NotificationEvent(
        id="1", timestamp="2026", category=NotificationCategory.STRATEGY, level=NotificationLevel.WARNING, title="매도",
        message="테스트 메시지", metadata={"return_rate": -3.2}
    )
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response
        await telegram_notifier.handle_event(event)
        payload = mock_post.call_args[1]["json"]
        assert "테스트 메시지\n📉 수익: -3.20%" in payload["text"]

@pytest.mark.asyncio
async def test_handle_event_return_rate_zero(telegram_notifier):
    """수익률이 0일 때 검증"""
    event = NotificationEvent(
        id="1", timestamp="2026", category=NotificationCategory.STRATEGY, level=NotificationLevel.ERROR, title="매도",
        message="테스트", metadata={"return_rate": 0.0}
    )
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response
        await telegram_notifier.handle_event(event)
        payload = mock_post.call_args[1]["json"]
        assert "➖ 수익: +0.00%" in payload["text"]

# --- TelegramReporter Tests ---

@pytest.fixture
def telegram_reporter():
    return TelegramReporter(report_bot_token="test_token", chat_id="test_chat_id")

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

@pytest.mark.asyncio
async def test_reporter_send_message_empty(telegram_reporter):
    """빈 텍스트 전송 시 API 호출 없이 True 반환 검증"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        result = await telegram_reporter._send_message("")
        assert result is True
        mock_post.assert_not_called()

@pytest.mark.asyncio
async def test_reporter_send_message_api_error(telegram_reporter, caplog):
    """Telegram API 에러 발생 (HTTP 400 등) 검증"""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text.return_value = "Bad Request"
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await telegram_reporter._send_message("test text")
        assert result is False
        assert "Telegram 리포트 전송 실패: 400 - Bad Request" in caplog.text

@pytest.mark.asyncio
async def test_reporter_send_message_exception(telegram_reporter, caplog):
    """연결 오류 등 예외 발생 시 검증"""
    with patch("aiohttp.ClientSession.post", side_effect=Exception("Connection Timeout")):
        result = await telegram_reporter._send_message("test text")
        assert result is False
        assert "Telegram 리포트 전송 중 예외 발생: Connection Timeout" in caplog.text

def test_format_ranking_table(telegram_reporter):
    """랭킹 테이블 포맷팅 검증"""
    data = [
        {'hts_kor_isnm': '삼성전자', 'value': '10000000000', 'acml_tr_pbmn': '100000000000', 'prdy_ctrt': '1.5'}, # 100억, 총거래대금 1000억
        {'hts_kor_isnm': 'SK하이닉스', 'value': '5000000000', 'acml_tr_pbmn': '100000000000', 'prdy_ctrt': '-2.0'}   # 50억, 총거래대금 1000억
    ]
    
    table = telegram_reporter._format_ranking_table("테스트 랭킹", data, "value")
    
    assert "🏆 테스트 랭킹" in table
    assert "<pre>" in table
    assert "금액(억)" in table
    assert "삼성전자" in table
    assert "100" in table # 100억
    assert "10.0%" in table # 비중 10%
    assert "+1.5%" in table # 등락 1.5%
    assert "50" in table  # 50억
    assert "5.0%" in table  # 비중 5%
    assert "-2.0%" in table # 등락 -2.0%

def test_format_ranking_table_empty_data(telegram_reporter):
    """데이터가 비어있을 때 빈 문자열 반환 검증"""
    assert telegram_reporter._format_ranking_table("Title", [], "val") == ""

def test_format_ranking_table_exceptions(telegram_reporter):
    """잘못된 데이터 형식이 들어왔을 때 예외 처리(try-except) 분기 검증"""
    data = [
        {'hts_kor_isnm': '오류1', 'value': 'not_a_number', 'acml_tr_pbmn': '0', 'prdy_ctrt': 'not_a_number'},
        {'hts_kor_isnm': '오류2', 'value': '100000000', 'acml_tr_pbmn': 'not_a_number', 'prdy_ctrt': '0'}
    ]
    table = telegram_reporter._format_ranking_table("Title", data, "value")
    
    # 첫 번째 항목 검증 (value 예외, rate 예외)
    assert "오류1" in table
    assert "-" in table  # 예외 시 '-' 할당 확인
    
    # 두 번째 항목 검증 (rate == 0, 비율 예외)
    assert "오류2" in table
    assert "0.0%" in table

@pytest.mark.asyncio
async def test_send_ranking_report_combined_ranking(telegram_reporter):
    """조합 랭킹(외인+기관, 외인+기관+프로그램) 단위 일치 및 계산 검증"""
    # 외인, 기관: 백만 원 단위 (예: 10000 -> 100억)
    # 프로그램: 원 단위 (예: 20000000000 -> 200억)
    all_stocks = [
        {
            'stck_shrn_iscd': '005930', 'hts_kor_isnm': '삼성전자',
            'frgn_ntby_tr_pbmn': '10000', # 100억 (백만 단위)
            'orgn_ntby_tr_pbmn': '5000',  # 50억 (백만 단위)
            'acml_tr_pbmn': '100000000000'
        }
    ]
    program_all_stocks = [
        {
            'stck_shrn_iscd': '005930',
            'whol_smtn_ntby_tr_pbmn': '20000000000' # 200억 (원 단위)
        }
    ]
    
    rankings = {
        'all_stocks': all_stocks,
        'program_all_stocks': program_all_stocks
    }
    
    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_ranking_report(rankings, "20250101")
    
    calls = telegram_reporter._send_message.call_args_list
    full_message = "".join([call[0][0] for call in calls])
    
    # 1. 외인+기관 = 100억 + 50억 = 150억. divisor=100이므로 150으로 표시
    assert "외인+기관 순매수" in full_message
    assert "150" in full_message
    
    # 2. 외인+기관+프로그램 = 100억(백만) + 50억(백만) + 200억(원 단위 -> 20000 백만)
    # 총 35000 (백만 단위, 350억). divisor=100이므로 350으로 표시
    assert "외인+기관+프로그램 순매수" in full_message
    assert "350" in full_message

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

@pytest.mark.asyncio
async def test_send_ranking_report_combined_ranking_exception(telegram_reporter):
    """조합 랭킹 계산 중 잘못된 데이터(문자열 등)가 있을 때 continue 분기 검증"""
    all_stocks = [
        {
            'stck_shrn_iscd': '005930', 'hts_kor_isnm': '정상종목',
            'frgn_ntby_tr_pbmn': '10000', 'orgn_ntby_tr_pbmn': '5000',
            'acml_tr_pbmn': '100000000000'
        },
        {
            'stck_shrn_iscd': '000660', 'hts_kor_isnm': '오류종목',
            'frgn_ntby_tr_pbmn': 'invalid', 'orgn_ntby_tr_pbmn': '5000',
            'acml_tr_pbmn': '100000000000'
        }
    ]
    rankings = {'all_stocks': all_stocks}
    
    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_ranking_report(rankings, "20250101")
    
    calls = telegram_reporter._send_message.call_args_list
    full_message = "".join([call[0][0] for call in calls])
    
    # 정상 종목만 계산에 포함되고 오류종목은 건너뛰어야 함
    assert "정상종목" in full_message
    assert "오류종목" not in full_message

@pytest.mark.asyncio
async def test_send_newhigh_report_with_historical_badge(telegram_reporter):
    """역신고가 뱃지가 포함된 52주 신고가 리포트 전송 검증"""
    stocks = [
        {"code": "005930", "name": "삼성전자", "current_price": 80000, "market_cap": 5000000000, "trading_value": 1000000000, "change_rate": 1.5, "is_historical_new_high": True},
        {"code": "000660", "name": "SK하이닉스", "current_price": 120000, "market_cap": 3000000000, "trading_value": 500000000, "change_rate": -1.0, "is_historical_new_high": False}
    ]
    
    telegram_reporter._send_message = AsyncMock(return_value=True)
    
    await telegram_reporter.send_newhigh_report(stocks, "2026-05-15")
    
    telegram_reporter._send_message.assert_called()
    calls = telegram_reporter._send_message.call_args_list
    full_message = "".join([call[0][0] for call in calls])
    
    assert "👑역 삼성전자" in full_message
    assert "👑역 SK하이닉스" not in full_message
    assert "SK하이닉스" in full_message