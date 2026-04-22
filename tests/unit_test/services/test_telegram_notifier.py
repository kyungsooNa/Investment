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


@pytest.mark.asyncio
async def test_handle_event_unsupported_category_returns_early(telegram_notifier, api_event):
    """허용 카테고리 제한이 없더라도 미지원 카테고리는 전송하지 않는다."""
    telegram_notifier.allowed_categories = None

    with patch("aiohttp.ClientSession.post") as mock_post:
        await telegram_notifier.handle_event(api_event)
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_handle_event_return_rate_none_keeps_message_body(telegram_notifier):
    """return_rate 키는 있지만 값이 None이면 메시지 본문을 수정하지 않는다."""
    event = NotificationEvent(
        id="1",
        timestamp="2026",
        category=NotificationCategory.STRATEGY,
        level=NotificationLevel.INFO,
        title="매도",
        message="원본 메시지",
        metadata={"return_rate": None},
    )
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response

        await telegram_notifier.handle_event(event)

        payload = mock_post.call_args[1]["json"]
        assert "원본 메시지" in payload["text"]
        assert "수익:" not in payload["text"]

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
    
    assert "👑역 <b>삼성전자</b>" in full_message
    assert "👑역 <b>SK하이닉스</b>" not in full_message
    assert "<b>SK하이닉스</b>" in full_message


@pytest.mark.asyncio
async def test_handle_event_unknown_level_default_emoji(telegram_notifier, sample_event):
    """알 수 없는 레벨 값일 때 기본 이모지가 사용되는지 검증"""
    # 이벤트의 level을 MagicMock으로 대체하여 .value 속성이 매핑되지 않게 함
    sample_event.level = MagicMock(value="MYSTIC")
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_post.return_value.__aenter__.return_value = mock_response

        await telegram_notifier.handle_event(sample_event)
        payload = mock_post.call_args[1]["json"]
        # 매핑되지 않는 레벨은 기본 이모지 '🔔' 사용
        assert "🔔" in payload["text"]


def test_format_ranking_table_no_ratio(telegram_reporter):
    """show_ratio=False 분기에서 비중 컬럼이 포함되지 않는지 검증"""
    data = [
        {'hts_kor_isnm': 'A', 'value': '100000000', 'prdy_ctrt': '2.0'},
    ]
    table = telegram_reporter._format_ranking_table("No Ratio", data, 'value', show_ratio=False)
    # '비중' 컬럼은 포함되지 않아야 함
    assert "비중" not in table
    assert "No Ratio" in table


def test_format_ranking_table_truncates_wide_name_and_handles_small_cap_ratio(telegram_reporter):
    """긴 전각 종목명 자르기와 작은 금액/비중 포맷을 검증한다."""
    data = [
        {
            'hts_kor_isnm': '가나다라마바사아자차카타파하',
            'value': '50000000',
            'acml_tr_pbmn': '100000000',
            'prdy_ctrt': '0',
        }
    ]

    table = telegram_reporter._format_ranking_table("긴 이름", data, "value", divisor=100_000_000)

    assert "가나다라" in table
    assert "마바사" not in table
    assert " 0 " in table or "\n1  가나다라    0.0%        0  50.0%\n" in table
    assert "50.0%" in table


@pytest.mark.asyncio
async def test_send_newhigh_report_empty(telegram_reporter):
    """빈 신고가 리스트일 때 '신고가 종목 없음' 메시지를 전송하는지 검증"""
    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_newhigh_report([], "2026-05-15")
    calls = telegram_reporter._send_message.call_args_list
    full = "".join([c[0][0] for c in calls])
    assert "신고가 종목 없음" in full


@pytest.mark.asyncio
async def test_send_newhigh_report_numeric_formatting_and_exceptions(telegram_reporter):
    """신고가 리포트의 시총/거래대금/등락률 예외 및 포맷 분기를 검증한다."""
    stocks = [
        {
            "code": "A",
            "name": "소형주",
            "current_price": 1000,
            "market_cap": 0.5,
            "trading_value": 50000000,
            "change_rate": "bad",
            "rs": 77,
        },
        {
            "code": "B",
            "name": "대형주",
            "current_price": 2000,
            "market_cap": 1500000000000,
            "trading_value": "bad",
            "change_rate": 1.25,
        },
    ]
    telegram_reporter._send_message = AsyncMock(return_value=True)

    await telegram_reporter.send_newhigh_report(stocks, "2026-05-15")

    full = "".join([c[0][0] for c in telegram_reporter._send_message.call_args_list])
    assert "0.5억" in full
    assert "1조 5,000억" in full
    assert "0.5억" in full
    assert "대금:-" in full
    assert "RS:77" in full
    assert "| RS:-" in full


@pytest.mark.asyncio
async def test_send_premium_watchlist_report_basic(telegram_reporter):
    """send_premium_watchlist_report: 정상 데이터 전송 검증"""
    kospi = [
        {"code": "005930", "name": "삼성전자", "total_score": 80.0, "rs_rating": 90,
         "market_cap": 400_000_000_000_000, "avg_trading_value_5d": 200_000_000_000, "minervini_stage": 2},
        {"code": "000660", "name": "SK하이닉스", "total_score": 60.0, "rs_rating": 75,
         "market_cap": 80_000_000_000_000, "avg_trading_value_5d": 50_000_000_000, "minervini_stage": 0},
    ]
    kosdaq = [
        {"code": "035420", "name": "NAVER", "total_score": 50.0, "rs_rating": 65,
         "market_cap": 30_000_000_000_000, "avg_trading_value_5d": 20_000_000_000, "minervini_stage": 0},
    ]

    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_premium_watchlist_report(kospi, kosdaq, "20260320")

    calls = telegram_reporter._send_message.call_args_list
    full = "".join([c[0][0] for c in calls])

    assert "전일 기준 우량주 리포트" in full
    assert "20260320" in full
    assert "KOSPI 2개" in full
    assert "KOSDAQ 1개" in full
    assert "삼성전자" in full
    assert "SK하이닉스" in full
    assert "NAVER" in full
    # Minervini Stage 2 종목에 ★ 뱃지
    assert "★" in full


@pytest.mark.asyncio
async def test_send_premium_watchlist_report_empty_markets(telegram_reporter):
    """send_premium_watchlist_report: KOSPI/KOSDAQ 중 하나가 빈 경우"""
    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_premium_watchlist_report([], [], "20260320")

    calls = telegram_reporter._send_message.call_args_list
    full = "".join([c[0][0] for c in calls])
    assert "전일 기준 우량주 리포트" in full
    assert "KOSPI 0개" in full
    assert "KOSDAQ 0개" in full


@pytest.mark.asyncio
async def test_send_premium_watchlist_report_market_cap_formatting(telegram_reporter):
    """send_premium_watchlist_report: 시가총액 조/억 단위 포맷팅 검증"""
    stocks = [
        {"code": "A", "name": "대형주", "total_score": 50.0, "rs_rating": 80,
         "market_cap": 50_000_000_000_000, "avg_trading_value_5d": 100_000_000_000, "minervini_stage": 0},
        {"code": "B", "name": "중형주", "total_score": 40.0, "rs_rating": 70,
         "market_cap": 500_000_000_000, "avg_trading_value_5d": 10_000_000_000, "minervini_stage": 0},
    ]

    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_premium_watchlist_report(stocks, [], "20260320")

    calls = telegram_reporter._send_message.call_args_list
    full = "".join([c[0][0] for c in calls])

    assert "50조" in full
    assert "5,000억" in full   # 500억 표시 (5000억)
    # avg_trading_value_5d 억 단위 변환
    assert "1,000억" in full   # 1000억
    assert "100억" in full


@pytest.mark.asyncio
async def test_send_premium_watchlist_report_invalid_numeric_fields(telegram_reporter):
    """send_premium_watchlist_report: 숫자 필드가 None/잘못된 값이어도 크래시 없이 '-' 표시"""
    stocks = [
        {"code": "A", "name": "오류종목", "total_score": None, "rs_rating": None,
         "market_cap": None, "avg_trading_value_5d": None, "minervini_stage": 0},
    ]

    telegram_reporter._send_message = AsyncMock(return_value=True)
    await telegram_reporter.send_premium_watchlist_report(stocks, [], "20260320")

    calls = telegram_reporter._send_message.call_args_list
    full = "".join([c[0][0] for c in calls])
    assert "오류종목" in full
    assert "-" in full  # 숫자 변환 실패 시 '-'


@pytest.mark.asyncio
async def test_send_strategy_log_report_splits_long_message(telegram_reporter):
    """전략 로그 리포트가 길면 여러 메시지로 분할 전송한다."""
    telegram_reporter._send_message = AsyncMock(return_value=True)
    long_line = "A" * 3900
    report_html = "\n".join([long_line, long_line, "tail"])

    await telegram_reporter.send_strategy_log_report(report_html, "2026-05-15")

    assert telegram_reporter._send_message.call_count >= 3
    first_text = telegram_reporter._send_message.call_args_list[0][0][0]
    assert "전략 실행 요약 리포트" in first_text


@pytest.mark.asyncio
async def test_send_premium_watchlist_report_splits_and_formats_exceptions(telegram_reporter):
    """우량주 리포트의 분할 전송과 숫자 포맷 예외 경로를 검증한다."""
    long_name = "종목" * 800
    stocks = [
        {
            "code": f"C{i}",
            "name": long_name,
            "total_score": 10.0,
            "rs_rating": 50,
            "market_cap": "bad" if i == 0 else 1000000000000,
            "avg_trading_value_5d": "bad" if i == 1 else 100000000000,
            "minervini_stage": 0,
        }
        for i in range(3)
    ]
    telegram_reporter._send_message = AsyncMock(return_value=True)

    await telegram_reporter.send_premium_watchlist_report(stocks, [], "20260320")

    full = "".join([c[0][0] for c in telegram_reporter._send_message.call_args_list])
    assert telegram_reporter._send_message.call_count >= 2
    assert "시총:-" in full
    assert "대금:-" in full


@pytest.mark.asyncio
async def test_send_minervini_report_formatting_exceptions_and_split(telegram_reporter):
    """Minervini 리포트의 가격/시총 예외 처리와 분할 전송을 검증한다."""
    telegram_reporter._send_message = AsyncMock(return_value=True)
    long_reason = "사유" * 1200
    items = [
        {
            "code": "A",
            "name": "예외종목",
            "stck_prpr": "bad",
            "rs": 88,
            "market_cap": "bad",
            "reason": long_reason,
        },
        {
            "code": "B",
            "name": "초대형주",
            "current_price": 12345,
            "rs_rating": 99,
            "market_cap": 1500000000000,
            "reason": "",
        },
    ]

    await telegram_reporter.send_minervini_report(items, "2026-05-15", limit=2)

    full = "".join([c[0][0] for c in telegram_reporter._send_message.call_args_list])
    assert telegram_reporter._send_message.call_count >= 2
    assert "예외종목" in full
    assert "초대형주" in full
    assert "| RS:88 | 시총:-" in full
    assert "1조 5,000억" in full
    assert "-" in full


@pytest.mark.asyncio
async def test_send_ranking_report_program_combined_exception_skips_bad_stock(telegram_reporter):
    """program_all_stocks 결합 계산 중 예외가 나면 해당 종목을 건너뛴다."""
    rankings = {
        "all_stocks": [
            {
                "stck_shrn_iscd": "005930",
                "hts_kor_isnm": "정상종목",
                "frgn_ntby_tr_pbmn": "100",
                "orgn_ntby_tr_pbmn": "200",
                "acml_tr_pbmn": "100000000",
            },
            {
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "오류종목",
                "frgn_ntby_tr_pbmn": "100",
                "orgn_ntby_tr_pbmn": "bad",
                "acml_tr_pbmn": "100000000",
            },
        ],
        "program_all_stocks": [
            {"stck_shrn_iscd": "005930", "whol_smtn_ntby_tr_pbmn": "100000000"},
            {"stck_shrn_iscd": "000660", "whol_smtn_ntby_tr_pbmn": "200000000"},
        ],
    }
    telegram_reporter._send_message = AsyncMock(return_value=True)

    await telegram_reporter.send_ranking_report(rankings, "20250101")

    full = "".join([call[0][0] for call in telegram_reporter._send_message.call_args_list])
    assert "정상종목" in full
    assert "오류종목" not in full


@pytest.mark.asyncio
async def test_send_minervini_report_empty_and_normal(telegram_reporter):
    """Minervini 리포트의 빈 리스트 처리와 정상 항목 전송 검증"""
    telegram_reporter._send_message = AsyncMock(return_value=True)

    # 빈 리스트 -> 결과 없음 메시지 전송
    await telegram_reporter.send_minervini_report([], "2026-05-15")
    assert telegram_reporter._send_message.called

    telegram_reporter._send_message.reset_mock()

    items = [
        {'code':'005930','name':'삼성전자','stck_prpr':'80000','rs_rating':'70','market_cap':'2000000000','reason':'테스트 사유'},
        {'code':'000660','name':'SK하이닉스','stck_prpr':None,'rs_rating':None,'market_cap':'5000000000000','reason':''}
    ]

    await telegram_reporter.send_minervini_report(items, "2026-05-15", limit=2)
    calls = telegram_reporter._send_message.call_args_list
    full_message = "".join([call[0][0] for call in calls])
    assert "Minervini Stage2 리포트" in full_message
    assert "삼성전자" in full_message
    assert "SK하이닉스" in full_message
    assert "테스트 사유" in full_message
