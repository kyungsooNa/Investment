# scheduler/after_market_loop.py
"""
장 마감 후 작업을 자동 실행하는 공통 스케줄러 루프.

RankingTask, DailyPriceCollectorTask 등 장 마감 후 1회 실행되는
백그라운드 태스크가 공유하는 스케줄링 패턴을 모듈화한다.

Usage::

    await run_after_market_loop(
        mcs=self._mcs,
        market_clock=self._market_clock,
        logger=self._logger,
        on_market_closed=self._do_work,  # async (latest_date: str) -> None
        label="MyTask",
    )
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable

from core.market_clock import MarketClock
from services.market_calendar_service import MarketCalendarService


async def run_after_market_loop(
    mcs: Optional[MarketCalendarService],
    market_clock: Optional[MarketClock],
    logger: Optional[logging.Logger],
    on_market_closed: Callable[[str], Awaitable[None]],
    label: str = "AfterMarketLoop",
    delay_sec: int = 0,
) -> None:
    """장 마감 후 작업을 자동으로 반복 실행하는 루프.

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
    """
    _log = logger or logging.getLogger(__name__)
    _log.info(f"[{label}] 장마감 후 자동 스케줄러 시작 (delay={delay_sec}s)")

    while True:
        try:
            # ── 1. 장 중이면 마감 시각까지 정확히 대기 ──
            if mcs and await mcs.is_market_open_now():
                wait_sec = (
                    market_clock.get_sleep_seconds_until_market_close()
                    if market_clock else 300
                )
                if wait_sec and wait_sec > 0:
                    _log.info(
                        f"[{label}] 장 마감까지 {wait_sec:.0f}초 대기"
                    )
                    await asyncio.sleep(wait_sec + 60)  # 마감 1분 뒤
                continue

            # ── 1b. 장 시작 전(09:00 이전)이면 마감 이후까지 대기 ──
            # is_market_open_now()는 장 중(09:00~15:40)에만 True를 반환하므로,
            # 09:00 이전과 15:40 이후를 구분하기 위해 별도로 확인한다.
            # 장 중이 아닌데도 마감까지 1시간(3600초) 이상 남아있다면 = 장 시작 전.
            if market_clock:
                secs_until_close = market_clock.get_sleep_seconds_until_market_close()
                if isinstance(secs_until_close, (int, float)) and secs_until_close > 3600:
                    _log.info(
                        f"[{label}] 장 시작 전 — 장 마감까지 {secs_until_close:.0f}초 대기 후 실행"
                    )
                    await asyncio.sleep(secs_until_close + 60)
                    continue

            # ── 2. 장 마감 이후 — Padding 대기 후 콜백 실행 ──
            if delay_sec > 0:
                _log.info(f"[{label}] 장 마감 감지 — {delay_sec}초 Padding 대기 후 실행")
                await asyncio.sleep(delay_sec)

            latest_trading_date = (
                await mcs.get_latest_trading_date() if mcs else None
            )
            if latest_trading_date:
                await on_market_closed(latest_trading_date)

            # ── 3. 스마트 대기: 다음 장 마감까지 ──
            await _smart_sleep(market_clock, _log, label)

        except asyncio.CancelledError:
            _log.info(f"[{label}] 장마감 후 스케줄러 종료")
            break
        except Exception as e:
            _log.error(f"[{label}] 스케줄러 오류: {e}", exc_info=True)
            await asyncio.sleep(60)


async def _smart_sleep(
    market_clock: Optional[MarketClock],
    logger: logging.Logger,
    label: str,
) -> None:
    """다음 장 마감까지 스마트하게 대기한다.

    - 아직 오늘 장 마감 전이면 → 마감+1분까지 정확히 대기
    - 이미 장 마감 지났으면 → 12시간 대기 (다음날 장 마감 전 기상)
    """
    wait_sec = (
        market_clock.get_sleep_seconds_until_market_close()
        if market_clock else 0
    )
    if wait_sec > 0:
        logger.info(
            f"[{label}] 다음 장 마감까지 {wait_sec / 3600:.1f}시간 대기"
        )
        await asyncio.sleep(wait_sec + 60)
    else:
        logger.info(
            f"[{label}] 오늘 작업 완료 또는 휴장. 12시간 대기"
        )
        await asyncio.sleep(12 * 3600)
