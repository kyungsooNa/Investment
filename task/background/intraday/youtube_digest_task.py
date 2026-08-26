"""장 시작 전 유튜브 채널 일일 다이제스트를 생성·저장·발송하는 태스크.

`TimeDispatcher` 를 쓰지 않는다. 그쪽은 `_is_after_market_close()` 로 장 마감
이후에만 발행하도록 되어 있어 07:30 을 표현할 수 없다. 대신 장 시작 전 작업의
선례인 `PreMarketHealthCheckTask` 처럼 자체 폴링 + 날짜 dedup 으로 게이팅한다.

운영 규칙 두 가지:

- **차단은 빈 리포트로 위장되면 안 된다.** IP 차단(IpBlocked)으로 자막을 못 받은
  날과 새 영상이 없던 날은 전혀 다른 상황이다. 차단은 ERROR 알림을 내고 저장하지
  않으며, 한 시간 뒤 한 번만 재시도한다(즉시 재시도는 차단을 악화시킨다).
- **수집 구간은 마지막 실행 이후로 늘어난다.** 주말·휴일을 건너뛴 월요일 아침에
  24시간만 보면 주말 업로드를 통째로 놓친다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Dict, List, Optional

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from services.notification_service import NotificationCategory, NotificationLevel


class YoutubeDigestTask(SchedulableTask):
    """07:30 KST 에 하루치 유튜브 다이제스트를 만든다."""

    POLL_INTERVAL_SEC = 60
    RUN_AT_HOUR = 7
    RUN_AT_MINUTE = 30
    # 재시작·일시정지로 정각을 놓쳐도 당일 안에 따라잡는다. 이 창을 넘기면 포기한다
    # (밤늦게 뒤늦게 발송되는 편이 더 나쁘다).
    CATCHUP_WINDOW_HOURS = 6
    # 차단 후 재시도까지 기다리는 시간. 즉시 재시도는 차단을 악화시킨다.
    RETRY_AFTER_BLOCK_MIN = 60
    MAX_BLOCK_RETRIES = 1
    # 마지막 실행 이후가 아무리 길어도 이보다 더 거슬러 올라가지 않는다.
    MAX_LOOKBACK_HOURS = 96
    DEFAULT_LOOKBACK_HOURS = 24
    MAX_VIDEOS_PER_CHANNEL = 3

    def __init__(
        self,
        *,
        channel_repository,
        collector,
        digest_service,
        digest_repository,
        notification_service=None,
        market_clock=None,
        logger: Optional[logging.Logger] = None,
        gemini_fallback_service=None,
    ) -> None:
        self._channel_repo = channel_repository
        self._collector = collector
        self._digest_service = digest_service
        self._digest_repo = digest_repository
        self._ns = notification_service
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._gemini_fallback_service = gemini_fallback_service
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._last_run_date: Optional[str] = None
        self._last_run_at = None
        self._last_report_date: Optional[str] = None
        self._last_error: Optional[str] = None
        self._block_retries = 0
        self._retry_after = None

    @property
    def task_name(self) -> str:
        return "youtube_digest"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    # --- 라이프사이클 -------------------------------------------------------

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        if self._state == TaskState.STOPPED:
            self._state = TaskState.IDLE
        self._tasks.append(asyncio.create_task(self._loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "last_report_date": self._last_report_date,
            "last_run_date": self._last_run_date,
            "last_error": self._last_error,
            "block_retries": self._block_retries,
        }

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.POLL_INTERVAL_SEC)
                if self._state == TaskState.SUSPENDED:
                    continue
                if await self._should_run_now():
                    self._state = TaskState.RUNNING
                    try:
                        await self.run_once()
                    finally:
                        if self._state == TaskState.RUNNING:
                            self._state = TaskState.IDLE
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[YoutubeDigest] 루프 오류: {e}", exc_info=True)

    # --- 게이팅 -------------------------------------------------------------

    async def _should_run_now(self) -> bool:
        if self._market_clock is None:
            return False
        now = self._market_clock.get_current_kst_time()
        if self._last_run_date == now.strftime("%Y%m%d"):
            return False
        if self._retry_after is not None and now < self._retry_after:
            return False
        start = now.replace(
            hour=self.RUN_AT_HOUR, minute=self.RUN_AT_MINUTE, second=0, microsecond=0
        )
        return start <= now < start + timedelta(hours=self.CATCHUP_WINDOW_HOURS)

    def _lookback_hours(self) -> float:
        """마지막 실행 이후 경과 시간. 주말을 건너뛴 뒤에도 공백이 생기지 않게 한다."""
        if self._last_run_at is None:
            return self.DEFAULT_LOOKBACK_HOURS
        now = self._market_clock.get_current_kst_time()
        elapsed = (now - self._last_run_at).total_seconds() / 3600
        return min(self.MAX_LOOKBACK_HOURS, max(self.DEFAULT_LOOKBACK_HOURS, elapsed))

    # --- 실행 ---------------------------------------------------------------

    async def run_once(self) -> None:
        now = self._market_clock.get_current_kst_time()
        report_date = now.strftime("%Y%m%d")
        self._last_error = None
        try:
            channels = await self._channel_repo.get_all(enabled_only=True)
            if not channels:
                self._logger.info("[YoutubeDigest] 등록된 채널이 없어 건너뜁니다.")
                self._mark_done(report_date, now)
                return

            items = await self._collector.collect(
                channels,
                since_hours=self._lookback_hours(),
                max_videos_per_channel=self.MAX_VIDEOS_PER_CHANNEL,
            )
            if getattr(self._collector, "was_blocked", False):
                if await self._try_gemini_fallback(items, report_date, now):
                    return
                await self._handle_block(report_date, now)
                return

            usable = [item for item in items if not item.get("skip_reason")]
            if not usable:
                self._logger.info("[YoutubeDigest] 자막을 확보한 새 영상이 없습니다.")
                await self._emit(
                    NotificationLevel.INFO,
                    "유튜브 다이제스트 - 새 영상 없음",
                    f"{report_date}: 자막을 확보한 영상이 없어 리포트를 건너뜁니다"
                    f" (수집 시도 {len(items)}편).",
                )
                self._mark_done(report_date, now)
                return

            result = await self._digest_service.build_digest(
                usable, report_date=report_date
            )
            if not result or result.rt_cd != ErrorCode.SUCCESS.value:
                message = getattr(result, "msg1", "응답 없음") if result else "응답 없음"
                self._last_error = message
                self._logger.warning(f"[YoutubeDigest] 리포트 생성 실패: {message}")
                await self._emit(
                    NotificationLevel.ERROR,
                    "유튜브 다이제스트 생성 실패",
                    f"{report_date}: {message}",
                )
                self._mark_done(report_date, now)
                return

            await self._digest_repo.save(result.data)
            self._last_report_date = report_date
            await self._emit(
                NotificationLevel.INFO,
                "유튜브 다이제스트",
                self._format_message(result.data),
            )
            await self._emit_partial_failure_warning(result.data)
            self._logger.info(
                f"[YoutubeDigest] 리포트 완료: {report_date} "
                f"영상 {result.data.get('video_count')}편"
            )
            self._mark_done(report_date, now)
        except Exception as e:
            self._last_error = str(e)
            self._logger.error(f"[YoutubeDigest] 실행 실패: {e}", exc_info=True)
            self._mark_done(report_date, now)

    async def _handle_block(self, report_date: str, now) -> None:
        self._last_error = "blocked"
        if self._block_retries < self.MAX_BLOCK_RETRIES:
            self._block_retries += 1
            self._retry_after = now + timedelta(minutes=self.RETRY_AFTER_BLOCK_MIN)
            self._logger.warning(
                f"[YoutubeDigest] YouTube 차단 — {self.RETRY_AFTER_BLOCK_MIN}분 뒤 재시도합니다."
            )
            return
        self._logger.error("[YoutubeDigest] YouTube 차단이 계속돼 오늘 리포트를 포기합니다.")
        await self._emit(
            NotificationLevel.ERROR,
            "유튜브 다이제스트 - YouTube 차단",
            f"{report_date}: YouTube 가 IP를 차단해 자막을 받지 못했습니다."
            " 오늘 리포트는 생성되지 않습니다.",
        )
        self._mark_done(report_date, now)

    async def _try_gemini_fallback(
        self, items: list[dict], report_date: str, now
    ) -> bool:
        if self._gemini_fallback_service is None:
            return False
        videos = [item for item in (items or []) if item.get("url")]
        if not videos:
            self._logger.warning("[YoutubeDigest] Gemini fallback 대상 영상이 없습니다.")
            return False
        try:
            result = await self._gemini_fallback_service.build_digest(
                videos, report_date=report_date
            )
        except Exception as e:
            self._last_error = f"blocked; gemini fallback failed: {e}"
            self._logger.warning(f"[YoutubeDigest] Gemini fallback 실패: {e}")
            return False
        if not result or result.rt_cd != ErrorCode.SUCCESS.value:
            message = getattr(result, "msg1", "응답 없음") if result else "응답 없음"
            self._last_error = f"blocked; gemini fallback failed: {message}"
            self._logger.warning(f"[YoutubeDigest] Gemini fallback 리포트 생성 실패: {message}")
            return False

        payload = dict(result.data or {})
        payload.setdefault("source", "gemini_video_url")
        await self._digest_repo.save(payload)
        self._last_report_date = report_date
        await self._emit(
            NotificationLevel.INFO,
            "유튜브 다이제스트",
            self._format_message(payload),
        )
        self._logger.info(
            f"[YoutubeDigest] Gemini fallback 리포트 완료: {report_date} "
            f"영상 {payload.get('video_count')}편"
        )
        self._mark_done(report_date, now)
        return True

    def _mark_done(self, report_date: str, now) -> None:
        self._last_run_date = report_date
        self._last_run_at = now
        self._block_retries = 0
        self._retry_after = None

    async def _emit(self, level: NotificationLevel, title: str, message: str) -> None:
        if self._ns is None:
            return
        await self._ns.emit(
            NotificationCategory.BACKGROUND,
            level,
            title,
            message,
            # 웹 알림함에만 쌓이면 아침에 못 본다. 텔레그램까지 확실히 보낸다.
            metadata={"force_external": True},
        )

    async def _emit_partial_failure_warning(self, data: dict) -> None:
        failed = int((data or {}).get("failed_summary_count") or 0)
        if failed <= 0:
            return
        total = int((data or {}).get("video_count") or 0)
        ratio = (failed / total * 100.0) if total > 0 else 0.0
        await self._emit(
            NotificationLevel.WARNING,
            "유튜브 다이제스트 - 일부 요약 실패",
            f"{data.get('report_date')}: 영상 요약 {failed}/{total}편 실패"
            f" ({ratio:.1f}%). 리포트에서 해당 영상의 맥락이 누락됐습니다.",
        )

    @staticmethod
    def _format_message(data: dict) -> str:
        mentions = data.get("mentions") or []
        top = ", ".join(
            f"{m['name']}({m['video_count']}편)" for m in mentions[:5]
        ) or "언급 종목 없음"
        return (
            f"{data.get('report_date')} · 영상 {data.get('video_count')}편\n"
            f"많이 언급: {top}\n\n"
            f"{data.get('digest_text') or ''}"
        )
