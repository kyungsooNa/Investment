"""TelegramReporter 리포트 포매팅·분할 전송 경로 테스트.

기존 테스트가 전송 성공/실패 자체를 다루므로, 여기서는 빈 데이터 조기 반환,
숫자 파싱 실패 fallback("-"), 4000바이트 초과 시 메시지 분할처럼 리포트 본문
조립에서만 갈라지는 분기를 채운다. 실제 HTTP 는 `_send_message` 를 대체해 막는다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import telegram_notifier as mod
from services.notification_service import (
    NotificationCategory,
    NotificationEvent,
    NotificationLevel,
)
from services.telegram_notifier import TelegramNotifier, TelegramReporter


@pytest.fixture
def reporter():
    instance = TelegramReporter(report_bot_token="token", chat_id="chat")
    instance._send_message = AsyncMock(return_value=True)
    return instance


def _sent(reporter):
    return [call.args[0] for call in reporter._send_message.await_args_list]


# --- 모듈 헬퍼 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("기재정정 주요사항보고서", "주요사항보고서"),
        ("[첨부정정] 사업보고서", "사업보고서"),
        ("정정·분기보고서", "분기보고서"),
        ("사업보고서", "사업보고서"),
    ],
)
def test_disclosure_report_type_strips_a_single_correction_prefix(raw, expected):
    assert mod._normalize_disclosure_report_type(raw) == expected


def test_digest_group_key_falls_back_to_the_receipt_number():
    item = SimpleNamespace(disclosure=SimpleNamespace(
        stock_code="005930", receipt_date="20260501", report_name="",
        receipt_no="R1", event_key=""))

    assert mod._disclosure_digest_group_key(item, 3) == ("receipt", "R1", "3")


def test_digest_summary_text_is_blank_when_the_item_has_none():
    assert mod._disclosure_digest_summary_text(SimpleNamespace(summary="  ")) == ""


def test_long_digest_summary_is_truncated_with_an_ellipsis():
    long_summary = "가" * 400

    text = mod._disclosure_digest_summary_text(SimpleNamespace(summary=long_summary))

    assert len(text) == mod._DISCLOSURE_DIGEST_SUMMARY_MAX_CHARS
    assert text.endswith("…")


def test_html_to_parts_uses_a_default_title_for_an_empty_body():
    assert mod._telegram_html_to_parts("<b>  </b>") == ("Telegram 알림", "")


def test_ai_summary_formatting_is_blank_for_empty_input():
    assert mod._format_disclosure_ai_summary_html("") == ""


def test_ai_summary_without_a_sentence_end_bolds_the_first_line():
    assert mod._format_disclosure_ai_summary_html("한 줄 요약\n다음 줄") == (
        "<b>한 줄 요약</b>\n다음 줄"
    )


def test_ai_summary_with_a_blank_lead_line_is_escaped_as_is():
    assert mod._format_disclosure_ai_summary_html("\n본문 <b>") == "\n본문 &lt;b&gt;"


# --- 숫자 포매터 -------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("숫자아님", "-"),
        (0, "-"),
        (150_000_000, "2억"),
        (1_500_000_000_000, "1조 5,000억"),
        (1_000_000_000_000_0, "10조"),
        (-1_500_000_000_000, "-2조 5,000억"),
    ],
)
def test_won_100m_formatting(raw, expected):
    assert TelegramReporter._format_won_100m(raw) == expected


@pytest.mark.parametrize("raw, expected", [("숫자아님", "-"), (None, "+0.0%"), (1.25, "+1.2%")])
def test_signed_pct_formatting(raw, expected):
    assert TelegramReporter._format_signed_pct(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("숫자아님", "-"),
        (0, "-"),
        (1_500_000_000_000, "2조"),
        (150_000_000, "2억"),
        (-150_000_000, "-2억"),
    ],
)
def test_krw_cap_formatting(raw, expected):
    assert TelegramReporter._format_krw_cap(raw) == expected


# --- 빈 데이터 조기 반환 ------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["send_period_investor_ranking_report", "send_ytd_ranking_report"]
)
async def test_ranking_reports_refuse_to_send_without_data(reporter, method):
    assert await getattr(reporter, method)([], "20260501") is False
    reporter._send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_disclosure_digest_without_items_is_a_no_op_success(reporter):
    assert await reporter.send_disclosure_digest([], "20260501") is True
    reporter._send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_theme_report_says_so_when_there_are_no_themes(reporter):
    await reporter.send_daily_theme_report([], "20260501")

    assert _sent(reporter)[-1] == "주도 테마 없음"


@pytest.mark.asyncio
async def test_newhigh_report_says_so_when_there_are_no_stocks(reporter):
    await reporter.send_newhigh_report([], "20260501")

    assert _sent(reporter)[-1] == "신고가 종목 없음"


# --- 랭킹 리포트 본문 ---------------------------------------------------------

@pytest.mark.asyncio
async def test_period_investor_ranking_is_sorted_by_the_combined_amount(reporter):
    await reporter.send_period_investor_ranking_report(
        [
            {"hts_kor_isnm": "작은종목", "combined_period_ntby_tr_pbmn_won": "100000000"},
            {"hts_kor_isnm": "큰종목", "combined_period_ntby_tr_pbmn_won": "900000000"},
        ],
        "20260501",
    )

    body = _sent(reporter)[0]
    assert body.index("큰종목") < body.index("작은종목")


@pytest.mark.asyncio
async def test_ytd_ranking_renders_a_dash_for_an_unparsable_price(reporter):
    await reporter.send_ytd_ranking_report(
        [{"name": "삼성전자", "current_price": "숫자아님", "ytd_return_rate": 12.5}],
        "20260501",
    )

    body = _sent(reporter)[0]
    assert "-" in body and "+12.50%" in body


@pytest.mark.asyncio
async def test_ytd_ranking_header_falls_back_to_the_report_date(reporter):
    await reporter.send_ytd_ranking_report([{"code": "005930"}], "20260501")

    assert "기준: - → 20260501" in _sent(reporter)[0]


# --- 테마 리포트 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_theme_report_renders_counts_bonus_and_thin_momentum_leaders(reporter):
    await reporter.send_daily_theme_report(
        [{
            "normalized_name": "반도체",
            "leader_avg_change_rate": 3.2,
            "trading_value_sum_won": 500_000_000_000,
            "advancing_ratio": 60.0,
            "market_leadership_score": 8.5,
            "scored_member_count": 10,
            "advance_count": 6,
            "liquidity_bonus": 1.25,
            "leaders": [{"code": "005930", "name": "삼성전자",
                         "change_rate": 3.0, "trading_value_won": 100_000_000_000}],
            "momentum_leaders": [{"code": "111111", "name": "소형주",
                                  "change_rate": 12.0, "trading_value_won": 100_000_000}],
        }],
        "20260501",
    )

    body = "\n".join(_sent(reporter))
    assert "상승 6/10" in body
    assert "유동성 +1.25" in body
    assert "주도점수 8.50" in body
    assert "상승률 상위(저유동성 포함)" in body and "소형주" in body


@pytest.mark.asyncio
async def test_theme_report_skips_unparsable_score_and_bonus(reporter):
    await reporter.send_daily_theme_report(
        [{"normalized_name": "반도체", "theme_score": "숫자아님",
          "liquidity_bonus": "숫자아님"}],
        "20260501",
    )

    body = "\n".join(_sent(reporter))
    assert "종합점수" not in body
    assert "유동성" not in body
    assert "상승비율" in body


@pytest.mark.asyncio
async def test_theme_report_splits_messages_past_the_size_limit(reporter):
    themes = [{"normalized_name": "테마" + "가" * 500} for _ in range(10)]

    await reporter.send_daily_theme_report(themes, "20260501", limit=10)

    assert reporter._send_message.await_count > 2


# --- 신고가 리포트 -----------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "market_cap, expected",
    [
        (2_000_000_000_000, "2조"),   # '원' 단위로 들어오면 억으로 보정
        (15_000, "1조 5,000억"),
        (0.5, "0.5억"),
        ("숫자아님", "-"),
        (0, "-"),
    ],
)
async def test_newhigh_report_market_cap_formatting(reporter, market_cap, expected):
    await reporter.send_newhigh_report(
        [{"name": "삼성전자", "code": "005930", "current_price": 75000,
          "market_cap": market_cap, "change_rate": "1.5"}],
        "20260501",
    )

    assert f"시총:{expected}" in "\n".join(_sent(reporter))


@pytest.mark.asyncio
async def test_newhigh_report_renders_dashes_for_unparsable_values(reporter):
    await reporter.send_newhigh_report(
        [{"name": "삼성전자", "code": "005930", "current_price": 75000,
          "trading_value": "숫자아님", "change_rate": "숫자아님",
          "is_historical_new_high": True}],
        "20260501",
    )

    body = "\n".join(_sent(reporter))
    assert "대금:-" in body
    assert "👑역" in body


@pytest.mark.asyncio
async def test_newhigh_report_splits_messages_past_the_size_limit(reporter):
    stocks = [{"name": "종" * 200, "code": f"{i:06d}", "current_price": 1000}
              for i in range(40)]

    await reporter.send_newhigh_report(stocks, "20260501")

    assert reporter._send_message.await_count > 2


# --- 텍스트 리포트 분할 -------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["send_strategy_log_report", "send_operational_decision_report"]
)
async def test_text_reports_split_long_bodies(reporter, method):
    body = "\n".join("가" * 500 for _ in range(20))

    await getattr(reporter, method)(body, "20260501")

    assert reporter._send_message.await_count > 2


@pytest.mark.asyncio
async def test_ranking_report_splits_parts_past_the_size_limit(reporter):
    rows = [{"hts_kor_isnm": "종목명" * 5, "stck_shrn_iscd": f"{i:06d}",
             "prdy_ctrt": "1.0", "acml_tr_pbmn": "100000000",
             "frgn_ntby_tr_pbmn": "100", "orgn_ntby_tr_pbmn": "200",
             "whol_smtn_ntby_tr_pbmn": "300"}
            for i in range(10)]
    rankings = {
        key: list(rows)
        for key in ("foreign_buy", "inst_buy", "program_buy", "foreign_sell",
                    "inst_sell", "program_sell", "trading_value",
                    "all_stocks", "program_all_stocks")
    }

    await reporter.send_ranking_report(rankings, "20260501")

    assert reporter._send_message.await_count > 2


# --- 우량주 / Minervini 리포트 -------------------------------------------------

@pytest.mark.asyncio
async def test_premium_watchlist_report_renders_dashes_and_favorite_ai_section(reporter):
    await reporter.send_premium_watchlist_report(
        kospi=[{"name": "삼성전자", "code": "005930", "total_score": 90,
                "market_cap": "숫자아님", "avg_trading_value_5d": "숫자아님",
                "minervini_stage": 2}],
        kosdaq=[],
        report_date="20260501",
        ai_analyses={
            "005930": {"signal": "매수", "signal_reason": "실적 개선"},
            "000660": {"source": "favorite", "name": "SK하이닉스",
                       "signal": "관망", "signal_reason": "밸류 부담"},
        },
    )

    body = "\n".join(_sent(reporter))
    assert "시총:- 대금:-" in body
    assert "★<b>삼성전자</b>" in body
    assert "🤖 AI:매수" in body
    assert "즐겨찾기 AI 분석" in body and "SK하이닉스" in body


@pytest.mark.asyncio
async def test_premium_watchlist_report_splits_long_market_sections(reporter):
    stocks = [{"name": "종" * 200, "code": f"{i:06d}", "total_score": 1}
              for i in range(40)]

    await reporter.send_premium_watchlist_report(stocks, [], "20260501", limit=40)

    assert reporter._send_message.await_count > 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "market_cap, expected",
    [(15_000, "1조 5,000억"), (500, "500억"), (0, "-"), ("숫자아님", "-")],
)
async def test_minervini_report_market_cap_formatting(reporter, market_cap, expected):
    await reporter.send_minervini_report(
        [{"name": "삼성전자", "code": "005930", "market_cap": market_cap}],
        "20260501",
    )

    assert f"시총:{expected}" in "\n".join(_sent(reporter))


# --- 시총갭 리포트 -----------------------------------------------------------

@pytest.mark.asyncio
async def test_market_cap_gap_report_handles_unparsable_ratios_and_gaps(reporter):
    await reporter.send_market_cap_gap_report(
        {
            "korean": [{"symbol": "005930", "name": "삼성전자",
                        "market_cap_krw": 500_000_000_000_000}],
            "comparisons": [{"us_symbol": "NVDA", "korean_symbol": "005930",
                             "korean_name": "삼성전자", "ratio": "숫자아님",
                             "gap_krw": "숫자아님"}],
        },
        "20260501",
        "장마감",
    )

    body = "\n".join(_sent(reporter))
    assert "NVDA" in body
    assert "<b>-</b>" in body


@pytest.mark.asyncio
async def test_market_cap_gap_report_marks_korean_advantage(reporter):
    await reporter.send_market_cap_gap_report(
        {
            "korean": [{"symbol": "005930", "name": "삼성전자",
                        "market_cap_krw": 500_000_000_000_000}],
            "us": [{"symbol": "NVDA", "market_cap_krw": 400_000_000_000_000}],
            "comparisons": [{"us_symbol": "NVDA", "korean_symbol": "005930",
                             "korean_name": "삼성전자", "ratio": 0.8,
                             "gap_krw": -100_000_000_000_000}],
        },
        "20260501",
        "장마감",
    )

    assert "✅" in "\n".join(_sent(reporter))


# --- 무역통계 리포트 위임 ------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_reports_delegate_to_the_shared_formatters(reporter, mocker):
    jeju = mocker.patch(
        "services.telegram_notifier.format_jeju_semiconductor_report_html",
        return_value="제주",
    )
    national = mocker.patch(
        "services.telegram_notifier.format_national_trade_trend_report_html",
        return_value="전국",
    )

    assert await reporter.send_jeju_semiconductor_trade_report(object()) is True
    assert await reporter.send_national_trade_trend_report(object()) is True

    jeju.assert_called_once()
    national.assert_called_once()
    assert _sent(reporter) == ["제주", "전국"]


# --- 알림 이력 저장 실패 격리 --------------------------------------------------

@pytest.mark.asyncio
async def test_report_history_write_failure_still_reports_success(mocker):
    history = MagicMock()
    history.record.side_effect = RuntimeError("db 없음")
    instance = TelegramReporter("token", "chat", history_repository=history)
    response = MagicMock()
    response.status = 200
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    session.post.return_value.__aexit__ = AsyncMock(return_value=False)
    client_session = MagicMock()
    client_session.__aenter__ = AsyncMock(return_value=session)
    client_session.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("services.telegram_notifier.aiohttp.ClientSession", return_value=client_session)
    mocker.patch("services.telegram_notifier.aiohttp.TCPConnector", return_value=MagicMock())

    assert await instance._send_message("<b>제목</b>\n본문") is True


@pytest.mark.asyncio
async def test_event_history_write_failure_does_not_break_delivery(mocker):
    history = MagicMock()
    history.record.side_effect = RuntimeError("db 없음")
    notifier = TelegramNotifier("s", "b", "chat", history_repository=history)
    response = MagicMock()
    response.status = 200
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    session.post.return_value.__aexit__ = AsyncMock(return_value=False)
    client_session = MagicMock()
    client_session.__aenter__ = AsyncMock(return_value=session)
    client_session.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("services.telegram_notifier.aiohttp.ClientSession", return_value=client_session)
    mocker.patch("services.telegram_notifier.aiohttp.TCPConnector", return_value=MagicMock())

    await notifier.handle_event(NotificationEvent(
        id="1", timestamp="2026-05-01T09:00:00",
        category=NotificationCategory.STRATEGY, level=NotificationLevel.INFO,
        title="매수", message="삼성전자 매수", metadata={},
    ))
