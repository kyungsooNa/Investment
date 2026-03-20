# scheduler/after_market_loop.py
"""
장 마감 후 작업을 자동 실행하는 공통 스케줄러 루프.

RankingTask, MarketDataCollectorTask 등 장 마감 후 1회 실행되는
백그라운드 태스크가 공유하는 스케줄링 패턴을 모듈화한다.

Usage::

    await run_after_market_loop(
        mdm=self._mdm,
        time_manager=self._time_manager,
        logger=self._logger,
        on_market_closed=self._do_work,  # async (latest_date: str) -> None
        label="MyTask",
    )
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable

from core.time_manager import TimeManager
from services.market_calendar_service import MarketCalendarService


async def run_after_market_loop(
    mdm: Optional[MarketCalendarService],
    time_manager: Optional[TimeManager],
    logger: Optional[logging.Logger],
    on_market_closed: Callable[[str], Awaitable[None]],
    label: str = "AfterMarketLoop",
) -> None:
    """장 마감 후 작업을 자동으로 반복 실행하는 루프.

    Args:
        mdm: 시장 개장/마감 판단용 MarketCalendar.
        time_manager: 장 마감까지 남은 시간 계산용 TimeManager.
        logger: 로깅용 Logger.
        on_market_closed: 장 마감 후 호출할 콜백.
            ``latest_trading_date`` (YYYYMMDD) 문자열을 받으며,
            내부에서 이미 처리한 날짜인지 직접 판단한다.
        label: 로그 메시지에 표시할 태스크 이름.
    """
    _log = logger or logging.getLogger(__name__)
    _log.info(f"[{label}] 장마감 후 자동 스케줄러 시작")

    while True:
        try:
            # ── 1. 장 중이면 마감 시각까지 정확히 대기 ──
            if mdm and await mdm.is_market_open_now():
                wait_sec = (
                    time_manager.get_sleep_seconds_until_market_close()
                    if time_manager else 300
                )
                if wait_sec and wait_sec > 0:
                    _log.info(
                        f"[{label}] 장 마감까지 {wait_sec:.0f}초 대기"
                    )
                    await asyncio.sleep(wait_sec + 60)  # 마감 1분 뒤
                continue

            # ── 2. 장 마감 이후 — 콜백 실행 ──
            latest_trading_date = (
                await mdm.get_latest_trading_date() if mdm else None
            )
            if latest_trading_date:
                await on_market_closed(latest_trading_date)

            # ── 3. 스마트 대기: 다음 장 마감까지 ──
            await _smart_sleep(time_manager, _log, label)

        except asyncio.CancelledError:
            _log.info(f"[{label}] 장마감 후 스케줄러 종료")
            break
        except Exception as e:
            _log.error(f"[{label}] 스케줄러 오류: {e}", exc_info=True)
            await asyncio.sleep(60)


async def _smart_sleep(
    time_manager: Optional[TimeManager],
    logger: logging.Logger,
    label: str,
) -> None:
    """다음 장 마감까지 스마트하게 대기한다.

    - 아직 오늘 장 마감 전이면 → 마감+1분까지 정확히 대기
    - 이미 장 마감 지났으면 → 12시간 대기 (다음날 장 마감 전 기상)
    """
    wait_sec = (
        time_manager.get_sleep_seconds_until_market_close()
        if time_manager else 0
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
