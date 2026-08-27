"""미국장 국면(마켓타이밍) 판정 서비스.

KIS Open API 에는 해외 지수 조회 TR 이 없다(국내 지수 TR 만 존재). 따라서 미국
시장 국면은 지수 자체가 아니라 **프록시 ETF 일봉**(기본 QQQ/NASD)으로 판정한다.
MA 추세 분류 로직 자체는 국내 `MarketRegimeService` 와 동일하며, 데이터 소스와
거래 캘린더만 해외용으로 갈아끼운다.

게이트 소비처: `OverseasIntradayVBOService` 의 신규 진입 판정. 청산(손절/EOD)은
국면과 무관하게 항상 동작한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional

from common.date_utils import previous_trading_day_str
from common.overseas_types import OverseasExchange
from core.market_clock import MarketClock
from services.market_regime_service import MarketRegimeConfig, MarketRegimeService
from services.notification_service import NotificationCategory, NotificationLevel
from services.stock_query_service import StockQueryService


@dataclass
class USMarketRegimeConfig(MarketRegimeConfig):
    """MA 파라미터는 국내와 동일하게 상속하고 데이터 소스만 교체한다.

    상속된 `kospi_index_code`/`kosdaq_index_code` 는 미장 경로에서 쓰이지 않는다.
    """
    proxy_symbol: str = "QQQ"
    proxy_exchange: OverseasExchange = OverseasExchange.NASD


class USMarketRegimeService(MarketRegimeService):
    """프록시 ETF 일봉으로 미국장 국면을 분류한다."""

    MARKET = "US"

    def __init__(
        self,
        stock_query_service: StockQueryService,
        market_clock: MarketClock,
        config: Optional[USMarketRegimeConfig] = None,
        logger: Optional[logging.Logger] = None,
        *,
        us_market_calendar_service=None,
        notification_service=None,
    ):
        super().__init__(
            stock_query_service=stock_query_service,
            market_clock=market_clock,
            config=config or USMarketRegimeConfig(),
            logger=logger,
        )
        self._calendar = us_market_calendar_service
        self._notification_service = notification_service

    # ── 데이터 소스 교체 ────────────────────────────────────────────────

    def _index_code_for(self, market: str) -> str:
        if market != self.MARKET:
            raise ValueError(f"Unknown market: {market}")
        return self._cfg.proxy_symbol

    async def _fetch_ohlcv(self, index_code: str, *, limit: int, end_date: Optional[str]):
        return await self._sqs.get_recent_daily_ohlcv(
            index_code,
            limit=limit,
            end_date=end_date,
            exchange=self._cfg.proxy_exchange,
        )

    def _previous_trading_day(self, now) -> str:
        """미국 거래 캘린더 기준 직전 거래일. 캘린더 미주입 시 주말만 건너뛴다."""
        if self._calendar is None:
            return previous_trading_day_str(now)
        check = (now.date() if hasattr(now, "date") else now) - timedelta(days=1)
        for _ in range(30):
            ds = check.strftime("%Y%m%d")
            try:
                if self._calendar.is_trading_day(ds):
                    return ds
            except Exception as e:
                self._logger.warning({"event": "us_market_regime_calendar_error", "error": str(e)})
                return previous_trading_day_str(now)
            check -= timedelta(days=1)
        return check.strftime("%Y%m%d")

    # ── 게이트/갱신 ────────────────────────────────────────────────────

    async def is_bull_us(self, logger: Optional[logging.Logger] = None) -> bool:
        return await self.is_bull(self.MARKET, logger=logger)

    async def refresh_market_timing(
        self,
        caller: str = "",
        logger: Optional[logging.Logger] = None,
    ) -> Dict[str, bool]:
        """캐시를 무효화하고 재분류한 뒤 로그·알림을 발행한다. 반환: {"US": bool}."""
        logger = logger or self._logger
        self._cache = {}
        self._cache_date = ""
        snap = await self.classify(self.MARKET, logger=logger)

        logger.info({
            "event": "us_market_timing_updated",
            "market": self.MARKET,
            "proxy": self._cfg.proxy_symbol,
            "ok": snap.is_rising,
            "regime_label": snap.regime_label,
            "fail_reason": snap.fail_detail if not snap.is_rising else "",
        })

        if self._notification_service:
            await self._emit_notification(snap, caller=caller)
        return {self.MARKET: snap.is_rising}

    async def _emit_notification(self, snap, *, caller: str) -> None:
        status_text = "🟢 매수 적합 (우상향)" if snap.is_rising else "🔴 매수 부적합 (추세 꺾임)"
        ma_str = " ➔ ".join([f"{v:.2f}" for v in snap.ma_values])
        msg = f"• 기준: 미국장 프록시 {self._cfg.proxy_symbol} ({self._cfg.proxy_exchange.value})\n"
        msg += f"• 상태: {status_text}\n"
        msg += f"• 데이터 기준일: {snap.data_date or '확인 불가'}\n"
        if snap.current_close is not None:
            msg += f"• 현재 종가: {snap.current_close:,.2f}\n"
        if not snap.is_rising and snap.fail_detail:
            msg += f"• 사유: {snap.fail_detail}\n"
        msg += f"• 최근 MA({self._cfg.ma_period}) 추이: {ma_str}"
        if not snap.is_rising and snap.recovery_earliest_days is not None:
            msg += f"\n• 최초 전환 가능: 최소 {snap.recovery_earliest_days}거래일 후 (이후 종가 조건 충족 시)"
        if not snap.is_rising and snap.next_close_floor is not None:
            msg += (
                f"\n• 다음 종가 하한: {snap.next_close_floor:,.2f} "
                f"(새 MA 급락 -{abs(self._cfg.hard_decline_pct):.2f}% 방지)"
            )

        title = "미국장 마켓 타이밍 갱신"
        if caller:
            title = f"[{caller}] {title}"
        await self._notification_service.emit(
            category=NotificationCategory.STRATEGY,
            level=NotificationLevel.INFO if snap.is_rising else NotificationLevel.WARNING,
            title=title,
            message=msg,
            metadata={
                "force_external": True,
                "event": "us_market_timing_updated",
                "market": self.MARKET,
                "proxy": self._cfg.proxy_symbol,
                "ok": snap.is_rising,
                "regime_label": snap.regime_label,
            },
        )
