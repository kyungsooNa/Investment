# scheduler/after_market_loop.py
"""
장 마감 후 작업을 자동 실행하는 공통 스케줄러 루프.

RankingTask, DailyPriceCollectorTask 등 장 마감 후 1회 실행되는
백그라운드 태스크가 공유하는 스케줄링 패턴을 모듈화한다.

Usage::

    loop = AfterMarketLoop(
        mcs=self._mcs,
        market_clock=self._market_clock,
        logger=self._logger,
        on_market_closed=self._do_work,  # async (latest_date: str) -> None
        label="MyTask",
    )
    await loop.start()   # stop()이 호출될 때까지 블로킹
    ...
    await loop.stop()

    # 또는 레거시 방식 (asyncio.create_task 호환):
    await run_after_market_loop(mcs=..., on_market_closed=..., label="MyTask")
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.market_clock import MarketClock
from services.market_calendar_service import MarketCalendarService

# 한국 장 마감 후 1분 뒤 트리거 (15:41 KST)
_CRON_HOUR = 15
_CRON_MINUTE = 41
# APScheduler misfire grace time: 이 시간(초) 이내 재구동 시 자동 보상 실행
_MISFIRE_GRACE_SEC = 3600


class AfterMarketLoop:
    """APScheduler CronTrigger 기반 장 마감 후 스케줄러.

    - 매 영업일 15:41 KST에 on_market_closed 콜백을 실행한다.
    - 프로그램 지연 구동(late start) 시 당일 배치 누락을 방지하는
      catch-up 로직을 포함한다.
    - stop()을 호출하면 진행 중인 sleep 없이 즉시 종료된다.
    """

    def __init__(
        self,
        mcs: Optional[MarketCalendarService],
        market_clock: Optional[MarketClock],
        logger: Optional[logging.Logger],
        on_market_closed: Callable[[str], Awaitable[None]],
        label: str = "AfterMarketLoop",
        delay_sec: int = 0,
        store=None,
        timezone: str = "Asia/Seoul",
        cron_hour: int = _CRON_HOUR,
        cron_minute: int = _CRON_MINUTE,
    ) -> None:
        """
        Args:
            store: StrategySchedulerStore 인스턴스 (선택).
                제공 시 last_run_date를 SQLite에 영속화하여
                catch-up 중복 실행을 방지한다.
                미제공 시 on_market_closed 콜백이 멱등성을 직접 책임진다.
            timezone: cron 트리거 타임존. 기본 Asia/Seoul(국내 마감).
                미국장 dry-run 등은 America/New_York 로 주입한다.
            cron_hour / cron_minute: 트리거 시각. 기본 15:41 KST.
                해당 타임존 기준 마감 직후 시각으로 주입한다.
        """
        self._mcs = mcs
        self._market_clock = market_clock
        self._log = logger or logging.getLogger(__name__)
        self._on_market_closed = on_market_closed
        self._label = label
        self._delay_sec = delay_sec
        self._store = store
        self._cron_hour = cron_hour
        self._cron_minute = cron_minute

        self._stop_event: asyncio.Event = asyncio.Event()
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone=timezone)

    # ── 생명주기 ──

    async def start(self) -> None:
        """스케줄러를 시작하고 stop()이 호출될 때까지 블로킹한다."""
        self._log.info(f"[{self._label}] AfterMarketLoop 시작 (APScheduler, delay={self._delay_sec}s)")
        self._stop_event.clear()

        await self._catch_up_if_needed()

        self._scheduler.add_job(
            self._run_job,
            "cron",
            day_of_week="mon-fri",
            hour=self._cron_hour,
            minute=self._cron_minute,
            misfire_grace_time=_MISFIRE_GRACE_SEC,
            id=f"after_market_{self._label}",
            replace_existing=True,
        )
        self._scheduler.start()

        await self._stop_event.wait()

    async def stop(self) -> None:
        """스케줄러를 정지한다 (async 버전)."""
        self.shutdown()

    def shutdown(self) -> None:
        """스케줄러를 정지한다 (동기 버전 — CancelledError 핸들러에서 사용 가능)."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._stop_event.set()
        self._log.info(f"[{self._label}] AfterMarketLoop 정지")

    # ── 지연 구동 catch-up ──

    async def _catch_up_if_needed(self) -> None:
        """프로그램 시작 시점이 15:41 이후이면, 당일 배치 누락 여부를 확인하고
        아직 실행하지 않았다면 즉시 1회 실행한다."""
        if not self._market_clock:
            return

        now_kst = self._market_clock.get_current_kst_time()
        past_close = now_kst.hour > self._cron_hour or (
            now_kst.hour == self._cron_hour and now_kst.minute >= self._cron_minute
        )
        if not past_close:
            return

        today_str = self._market_clock.get_current_kst_date_str()

        # 오늘 이미 실행했으면 스킵
        last_run = self._load_last_run_date()
        if last_run == today_str:
            self._log.info(f"[{self._label}] 오늘({today_str}) 이미 실행 완료 — catch-up 스킵")
            return

        # 오늘이 거래일인지 확인 (latest_trading_date == today_str)
        if self._mcs:
            latest = await self._mcs.get_latest_trading_date()
            if latest != today_str:
                self._log.info(f"[{self._label}] 오늘({today_str})은 휴장일 — catch-up 스킵")
                return

        self._log.info(f"[{self._label}] 지연 구동 감지 — 오늘자 장마감 배치 즉시 실행")
        asyncio.create_task(self._run_job())

    # ── 실제 작업 실행 ──

    async def _run_job(self) -> None:
        """APScheduler 또는 catch-up에 의해 호출되는 실제 작업 실행 메서드."""
        today_str = (
            self._market_clock.get_current_kst_date_str() if self._market_clock else None
        )
        # mcs(거래 캘린더) 미주입 시 클럭 날짜를 거래일 식별자로 사용한다.
        # 미국장 dry-run 은 한국 캘린더가 없으므로 cron 의 mon-fri 필터 + 클럭 날짜로 대체.
        latest_trading_date = (
            await self._mcs.get_latest_trading_date() if self._mcs else today_str
        )
        if not latest_trading_date:
            return

        if today_str and latest_trading_date != today_str:
            self._log.info(
                f"[{self._label}] 오늘({today_str})은 휴장일 — 콜백 스킵 (latest={latest_trading_date})"
            )
            return

        if self._delay_sec > 0:
            self._log.info(f"[{self._label}] 장 마감 감지 — {self._delay_sec}초 Padding 대기 후 실행")
            await asyncio.sleep(self._delay_sec)

        try:
            await self._on_market_closed(latest_trading_date)
            self._save_last_run_date(today_str or latest_trading_date)
        except Exception as e:
            self._log.error(f"[{self._label}] 콜백 오류: {e}", exc_info=True)

    # ── 상태 영속화 ──

    def _load_last_run_date(self) -> Optional[str]:
        if not self._store:
            return None
        return self._store.load_keyed(f"after_market_last_run_{self._label}")

    def _save_last_run_date(self, date_str: str) -> None:
        if not self._store:
            return
        self._store.save_keyed(f"after_market_last_run_{self._label}", date_str)


# ── 레거시 호환 래퍼 ──

async def run_after_market_loop(
    mcs: Optional[MarketCalendarService],
    market_clock: Optional[MarketClock],
    logger: Optional[logging.Logger],
    on_market_closed: Callable[[str], Awaitable[None]],
    label: str = "AfterMarketLoop",
    delay_sec: int = 0,
    timezone: str = "Asia/Seoul",
    cron_hour: int = _CRON_HOUR,
    cron_minute: int = _CRON_MINUTE,
) -> None:
    """하위 호환 래퍼 — 기존 코드는 이 함수를 asyncio.create_task()로 실행한다.

    Args:
        mcs: 시장 개장/마감 판단용 MarketCalendar.
        market_clock: 장 마감까지 남은 시간 계산용 MarketClock.
        logger: 로깅용 Logger.
        on_market_closed: 장 마감 후 호출할 콜백.
            ``latest_trading_date`` (YYYYMMDD) 문자열을 받으며,
            내부에서 이미 처리한 날짜인지 직접 판단한다.
        label: 로그 메시지에 표시할 태스크 이름.
        delay_sec: 장 마감 감지 후 콜백 실행 전까지의 Padding 시간(초).
            여러 태스크의 실행 시점을 분산시킬 때 사용한다.
        timezone / cron_hour / cron_minute: cron 트리거 타임존·시각.
            기본 Asia/Seoul 15:41(국내 마감). 미국장 등은 별도 주입.
    """
    loop = AfterMarketLoop(
        mcs=mcs,
        market_clock=market_clock,
        logger=logger,
        on_market_closed=on_market_closed,
        label=label,
        delay_sec=delay_sec,
        timezone=timezone,
        cron_hour=cron_hour,
        cron_minute=cron_minute,
    )
    try:
        await loop.start()
    except asyncio.CancelledError:
        loop.shutdown()
        raise
    except Exception:
        loop.shutdown()
        raise
