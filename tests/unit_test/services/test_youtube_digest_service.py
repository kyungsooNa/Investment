"""유튜브 다이제스트 서비스 단위 테스트."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode
from services.youtube_digest_service import YoutubeDigestService


class _FakeCodeRepo:
    def __init__(self, name_to_code: dict):
        self.name_to_code = dict(name_to_code)


_NAMES = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "SK": "034730",
    "LG전자": "066570",
    "네이버": "035420",
    "대상": "001680",
    "이닉스": "452400",
    "코리아": "999999",
}


def _item(video_id: str, transcript: str, **kwargs) -> dict:
    base = {
        "video_id": video_id,
        "title": f"{video_id} 제목",
        "channel_title": "테스트채널",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "published": "2026-08-10T07:00:00+00:00",
        "transcript": transcript,
        "skip_reason": None,
    }
    base.update(kwargs)
    return base


def _service(ai_client=None, **kwargs) -> YoutubeDigestService:
    return YoutubeDigestService(
        ai_client or MagicMock(),
        stock_code_repository=_FakeCodeRepo(_NAMES),
        **kwargs,
    )


def test_longest_match_prevents_substring_false_positive():
    """'SK 하이닉스'는 SK하이닉스 1건이어야 한다 — 안쪽의 '이닉스'를 따로 세면 안 된다."""
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "오늘은 SK 하이닉스 얘기입니다.")])

    assert [(m["name"], m["count"]) for m in mentions] == [("SK하이닉스", 1)]


def test_two_char_names_are_excluded_as_asr_noise():
    """2글자 이름은 자막 어절 파편과 충돌해 오검출이 압도적이다 (실측: 레이 59회 전량 오검출)."""
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "SK 주가가 올랐습니다.")])

    assert mentions == []


def test_left_boundary_blocks_match_inside_a_word():
    """'하이닉스'의 뒷부분이 '이닉스' 언급으로 잡히면 안 된다 (실측 13회 오검출)."""
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "하이닉스가 크게 올랐습니다.")])

    assert mentions == []


def test_left_boundary_allows_match_after_punctuation():
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "(네이버)와 '삼성전자'를 봅니다.")])

    assert {m["name"] for m in mentions} == {"네이버", "삼성전자"}


def test_korean_particles_do_not_block_match():
    """'삼성전자가/를/는' 처럼 조사가 붙어도 언급으로 세야 한다."""
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "삼성전자가 오르고 삼성전자를 봅니다")])

    assert mentions[0]["count"] == 2


def test_right_boundary_blocks_match_inside_following_word():
    """종목명 뒤에 다른 단어가 바로 붙으면 종목 언급으로 보지 않는다."""
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "삼성전자서비스센터 이야기를 합니다.")])

    assert mentions == []


def test_ambiguous_common_word_names_are_excluded():
    """'코리아'는 일반명사처럼 쓰여 카운트가 무의미해진다."""
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "코리아 디스카운트 얘기와 네이버입니다.")])

    assert {m["name"] for m in mentions} == {"네이버"}


def test_mentions_aggregate_count_and_video_spread():
    svc = _service()
    items = [
        _item("v1", "삼성전자 삼성전자 네이버"),
        _item("v2", "삼성전자 LG전자"),
    ]

    mentions = svc.count_stock_mentions(items)

    by_name = {m["name"]: m for m in mentions}
    assert by_name["삼성전자"]["count"] == 3
    assert by_name["삼성전자"]["video_count"] == 2
    assert by_name["네이버"]["video_count"] == 1


def test_mentions_sorted_by_video_count_then_count():
    """여러 방송이 공통으로 말한 종목이 한 방송의 반복보다 위여야 한다."""
    svc = _service()
    items = [
        _item("v1", "삼성전자 " * 10),
        _item("v2", "네이버 LG전자"),
        _item("v3", "네이버 LG전자"),
    ]

    mentions = svc.count_stock_mentions(items)

    assert [m["name"] for m in mentions[:2]] == ["LG전자", "네이버"]
    assert mentions[2]["name"] == "삼성전자"


def test_mentions_include_code_for_downstream_linking():
    svc = _service()

    mentions = svc.count_stock_mentions([_item("v1", "네이버 좋습니다")])

    assert mentions[0]["code"] == "035420"


def test_skipped_items_are_not_counted():
    svc = _service()

    mentions = svc.count_stock_mentions(
        [_item("v1", "", skip_reason="transcript_unavailable")]
    )

    assert mentions == []


def test_count_without_code_repository_returns_empty():
    svc = YoutubeDigestService(MagicMock(), stock_code_repository=None)

    assert svc.count_stock_mentions([_item("v1", "삼성전자")]) == []


async def test_summarize_video_single_call_for_short_transcript():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value="요약")
    svc = _service(ai, chunk_chars=100)

    summary = await svc.summarize_video(_item("v1", "짧은 자막"))

    assert summary == "요약"
    assert ai.complete.await_count == 1


async def test_summarize_video_chunks_long_transcript():
    """실측상 자막이 34,000자까지 나온다 — 입력 예산 안으로 쪼개 호출해야 한다."""
    ai = MagicMock()
    ai.complete = AsyncMock(side_effect=["앞부분", "뒷부분"])
    svc = _service(ai, chunk_chars=100)

    summary = await svc.summarize_video(_item("v1", "가" * 200))

    assert ai.complete.await_count == 2
    assert "앞부분" in summary and "뒷부분" in summary
    for call in ai.complete.await_args_list:
        assert len(call.kwargs["user"]) <= 100 + 500  # 청크 + 프롬프트 여유


async def test_build_digest_returns_error_when_no_usable_video():
    ai = MagicMock()
    ai.complete = AsyncMock()
    svc = _service(ai)

    result = await svc.build_digest(
        [_item("v1", "", skip_reason="empty_transcript")], report_date="20260810"
    )

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    ai.complete.assert_not_awaited()


async def test_build_digest_produces_report_payload():
    ai = MagicMock()
    ai.complete = AsyncMock(side_effect=["v1 요약", "v2 요약", "종합 리포트"])
    svc = _service(ai)

    result = await svc.build_digest(
        [_item("v1", "삼성전자 이야기"), _item("v2", "삼성전자 네이버 이야기")],
        report_date="20260810",
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    data = result.data
    assert data["report_date"] == "20260810"
    assert data["video_count"] == 2
    assert data["digest_text"] == "종합 리포트"
    assert data["mentions"][0]["name"] == "삼성전자"
    assert len(data["videos"]) == 2
    assert data["videos"][0]["summary"] == "v1 요약"


async def test_build_digest_keeps_going_when_one_video_summary_fails():
    ai = MagicMock()
    ai.complete = AsyncMock(side_effect=[RuntimeError("upstream 503"), "v2 요약", "종합"])
    svc = _service(ai)

    result = await svc.build_digest(
        [_item("v1", "삼성전자"), _item("v2", "네이버")], report_date="20260810"
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    summaries = [v["summary"] for v in result.data["videos"]]
    assert summaries[0] == ""
    assert summaries[1] == "v2 요약"
    assert result.data["failed_summary_count"] == 1


async def test_build_digest_reports_error_when_final_call_fails():
    ai = MagicMock()
    ai.complete = AsyncMock(side_effect=["v1 요약", RuntimeError("upstream 503")])
    svc = _service(ai)

    result = await svc.build_digest([_item("v1", "삼성전자")], report_date="20260810")

    assert result.rt_cd == ErrorCode.API_ERROR.value


async def test_digest_prompt_forbids_trading_instructions():
    """유튜버 발언 집계일 뿐 매매 지시가 아니라는 제약이 프롬프트에 있어야 한다."""
    ai = MagicMock()
    ai.complete = AsyncMock(return_value="요약")
    svc = _service(ai)

    await svc.summarize_video(_item("v1", "삼성전자"))

    system = ai.complete.await_args.kwargs["system"]
    assert "매수" in system and "지시" in system


async def test_digest_prompt_requires_uncertain_claims_section():
    """뉴스성 주장·목표가·거시 수치는 확인 필요로 분리해야 한다."""
    ai = MagicMock()
    ai.complete = AsyncMock(side_effect=["영상 요약", "종합"])
    svc = _service(ai)

    await svc.build_digest([_item("v1", "삼성전자 목표가 32만원")], report_date="20260810")

    system = ai.complete.await_args.kwargs["system"]
    assert "목표가" in system
    assert "거시 수치" in system
    assert "뉴스성 주장" in system
    assert "확인이 필요한 주장" in system


async def test_ai_calls_are_tagged_with_usage_type():
    """/api/ai/usage 집계에 유튜브 소비가 잡혀야 한다."""
    ai = MagicMock()
    ai.complete = AsyncMock(return_value="요약")
    svc = _service(ai)

    await svc.summarize_video(_item("v1", "삼성전자"))

    assert ai.complete.await_args.kwargs["usage_type"] == "youtube"
