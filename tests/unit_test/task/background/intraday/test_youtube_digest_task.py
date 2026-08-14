"""유튜브 일일 다이제스트 스케줄 태스크 단위 테스트."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from task.background.intraday.youtube_digest_task import YoutubeDigestTask

_KST = pytz.timezone("Asia/Seoul")


def _clock(hour: int, minute: int = 0, day: int = 10) -> MagicMock:
    clock = MagicMock()
    clock.get_current_kst_time.return_value = _KST.localize(
        datetime(2026, 8, day, hour, minute)
    )
    return clock


def _ok_digest(**kwargs) -> ResCommonResponse:
    data = {
        "report_date": "20260810",
        "video_count": 2,
        "failed_summary_count": 0,
        "mentions": [{"name": "삼성전자", "code": "005930", "count": 4, "video_count": 2}],
        "videos": [],
        "digest_text": "오늘의 화두",
    }
    data.update(kwargs)
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _task(
    *,
    clock=None,
    collect_result=None,
    was_blocked=False,
    digest_result=None,
    gemini_fallback_result=None,
    channels=None,
):
    channel_repo = MagicMock()
    channel_repo.get_all = AsyncMock(
        return_value=channels if channels is not None else [{"channel_id": "UC1", "title": "채널"}]
    )
    collector = MagicMock()
    collector.collect = AsyncMock(
        return_value=collect_result
        if collect_result is not None
        else [{"video_id": "v1", "skip_reason": None, "transcript": "본문"}]
    )
    collector.was_blocked = was_blocked
    digest_service = MagicMock()
    digest_service.build_digest = AsyncMock(
        return_value=digest_result if digest_result is not None else _ok_digest()
    )
    digest_repo = MagicMock()
    digest_repo.save = AsyncMock()
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    gemini_fallback = None
    if gemini_fallback_result is not None:
        gemini_fallback = MagicMock()
        gemini_fallback.build_digest = AsyncMock(return_value=gemini_fallback_result)

    task = YoutubeDigestTask(
        channel_repository=channel_repo,
        collector=collector,
        digest_service=digest_service,
        digest_repository=digest_repo,
        notification_service=notifier,
        market_clock=clock or _clock(7, 30),
        gemini_fallback_service=gemini_fallback,
    )
    return task, {
        "channel_repo": channel_repo,
        "collector": collector,
        "digest_service": digest_service,
        "digest_repo": digest_repo,
        "notifier": notifier,
        "gemini_fallback": gemini_fallback,
    }


# --- 실행 시각 게이팅 -------------------------------------------------------


@pytest.mark.parametrize("hour,minute,expected", [
    (7, 29, False),
    (7, 30, True),
    (9, 0, True),     # 재시작 늦어도 당일 안에 따라잡는다
    (13, 29, True),
    (13, 30, False),  # 캐치업 창을 지나면 그날은 포기
    (23, 0, False),
])
async def test_run_window(hour, minute, expected):
    task, _ = _task(clock=_clock(hour, minute))

    assert await task._should_run_now() is expected


async def test_does_not_run_twice_on_the_same_day():
    task, deps = _task(clock=_clock(7, 30))

    assert await task._should_run_now() is True
    await task.run_once()

    assert await task._should_run_now() is False


async def test_runs_again_on_the_next_day():
    clock = _clock(7, 30, day=10)
    task, _ = _task(clock=clock)
    await task.run_once()

    clock.get_current_kst_time.return_value = _KST.localize(datetime(2026, 8, 11, 7, 30))

    assert await task._should_run_now() is True


# --- 정상 경로 --------------------------------------------------------------


async def test_run_once_saves_and_notifies():
    task, deps = _task()

    await task.run_once()

    deps["digest_repo"].save.assert_awaited_once()
    saved = deps["digest_repo"].save.await_args.args[0]
    assert saved["report_date"] == "20260810"
    deps["notifier"].emit.assert_awaited_once()


async def test_report_is_pushed_to_telegram():
    """웹 알림함에만 남으면 안 된다 — 외부 발송을 강제해야 한다."""
    task, deps = _task()

    await task.run_once()

    metadata = deps["notifier"].emit.await_args.kwargs["metadata"]
    assert metadata["force_external"] is True


async def test_only_enabled_channels_are_collected():
    task, deps = _task()

    await task.run_once()

    deps["channel_repo"].get_all.assert_awaited_once_with(enabled_only=True)


async def test_skipped_videos_are_not_sent_to_the_digest():
    collected = [
        {"video_id": "v1", "skip_reason": None, "transcript": "본문"},
        {"video_id": "live", "skip_reason": "transcript_unavailable", "transcript": ""},
    ]
    task, deps = _task(collect_result=collected)

    await task.run_once()

    passed = deps["digest_service"].build_digest.await_args.args[0]
    assert [item["video_id"] for item in passed] == ["v1"]


# --- 주말 건너뛴 뒤 수집 구간 -------------------------------------------------


async def test_first_run_looks_back_one_day():
    task, deps = _task()

    await task.run_once()

    assert deps["collector"].collect.await_args.kwargs["since_hours"] == 24


async def test_lookback_covers_the_gap_since_the_last_run():
    """월요일 07:30 실행이 주말 영상을 놓치면 안 된다."""
    clock = _clock(7, 30, day=7)  # 금요일
    task, deps = _task(clock=clock)
    await task.run_once()

    clock.get_current_kst_time.return_value = _KST.localize(datetime(2026, 8, 10, 7, 30))
    await task.run_once()

    assert deps["collector"].collect.await_args.kwargs["since_hours"] == 72


# --- 차단·빈 결과 -----------------------------------------------------------


async def test_ip_block_never_produces_an_empty_report():
    """차단은 '오늘 영상 없음'이 아니다 — 빈 리포트를 저장·발송하면 안 된다."""
    task, deps = _task(collect_result=[], was_blocked=True)

    await task.run_once()

    deps["digest_service"].build_digest.assert_not_awaited()
    deps["digest_repo"].save.assert_not_awaited()


async def test_first_block_stays_quiet_because_it_will_be_retried():
    """일시적 차단마다 알림을 쏘면 경보 피로만 생긴다."""
    task, deps = _task(collect_result=[], was_blocked=True)

    await task.run_once()

    deps["notifier"].emit.assert_not_awaited()


async def test_persistent_block_notifies_error():
    clock = _clock(7, 30)
    task, deps = _task(clock=clock, collect_result=[], was_blocked=True)

    await task.run_once()
    clock.get_current_kst_time.return_value = _KST.localize(datetime(2026, 8, 10, 8, 31))
    await task.run_once()

    deps["digest_repo"].save.assert_not_awaited()
    assert "차단" in deps["notifier"].emit.await_args.args[2]
    assert deps["notifier"].emit.await_args.args[1].value == "error"


async def test_ip_block_is_retried_once_before_giving_up():
    """한 번 막혔다고 그날 리포트를 버리기엔 아깝다 — 뒤로 미뤄 1회만 재시도한다."""
    clock = _clock(7, 30)
    task, deps = _task(clock=clock, collect_result=[], was_blocked=True)

    await task.run_once()
    assert await task._should_run_now() is False  # 재시도 대기 중

    clock.get_current_kst_time.return_value = _KST.localize(datetime(2026, 8, 10, 8, 31))
    assert await task._should_run_now() is True

    await task.run_once()  # 두 번째도 차단
    clock.get_current_kst_time.return_value = _KST.localize(datetime(2026, 8, 10, 10, 0))
    assert await task._should_run_now() is False  # 포기


async def test_ip_block_uses_gemini_fallback_when_configured():
    """Gemini fallback 이 켜져 있으면 IP 차단을 빈 리포트로 끝내지 않는다."""
    fallback = _ok_digest(
        video_count=1,
        mentions=[],
        videos=[{
            "video_id": "v1",
            "title": "영상",
            "channel_title": "채널",
            "url": "https://www.youtube.com/watch?v=v1",
            "published": "2026-08-10T07:00:00+00:00",
            "summary": "Gemini 요약",
        }],
        digest_text="Gemini fallback 리포트",
        source="gemini_video_url",
    )
    task, deps = _task(
        collect_result=[{
            "video_id": "v1",
            "title": "영상",
            "channel_title": "채널",
            "url": "https://www.youtube.com/watch?v=v1",
            "published": "2026-08-10T07:00:00+00:00",
            "skip_reason": "blocked",
            "transcript": "",
        }],
        was_blocked=True,
        gemini_fallback_result=fallback,
    )

    await task.run_once()

    deps["gemini_fallback"].build_digest.assert_awaited_once()
    deps["digest_repo"].save.assert_awaited_once()
    saved = deps["digest_repo"].save.await_args.args[0]
    assert saved["source"] == "gemini_video_url"
    assert task.get_progress()["last_report_date"] == "20260810"


async def test_no_usable_video_notifies_without_calling_ai():
    task, deps = _task(collect_result=[
        {"video_id": "v1", "skip_reason": "transcript_unavailable", "transcript": ""}
    ])

    await task.run_once()

    deps["digest_service"].build_digest.assert_not_awaited()
    deps["digest_repo"].save.assert_not_awaited()
    deps["notifier"].emit.assert_awaited_once()


async def test_no_registered_channel_is_a_noop():
    task, deps = _task(channels=[])

    await task.run_once()

    deps["collector"].collect.assert_not_awaited()


async def test_digest_failure_notifies_error_and_does_not_save():
    failed = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="AI 실패", data=None)
    task, deps = _task(digest_result=failed)

    await task.run_once()

    deps["digest_repo"].save.assert_not_awaited()
    assert deps["notifier"].emit.await_args.args[1].value == "error"


async def test_unexpected_error_does_not_escape_the_loop():
    task, deps = _task()
    deps["collector"].collect = AsyncMock(side_effect=RuntimeError("boom"))

    await task.run_once()

    assert task.get_progress()["last_error"] == "boom"


# --- 라이프사이클 -----------------------------------------------------------


async def test_start_and_stop_clean_up_the_loop():
    task, _ = _task()

    await task.start()
    assert task.get_progress()["running"] is False
    await task.stop()

    assert task.state.name == "STOPPED"


def test_progress_exposes_required_keys():
    task, _ = _task()

    progress = task.get_progress()

    assert "running" in progress
    assert "last_report_date" in progress
