from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

import httpx

from common.types import ErrorCode


@dataclass(frozen=True)
class MarketCapQuote:
    symbol: str
    name: str
    currency: str
    market_cap: int


class UsMarketCapProvider(Protocol):
    async def fetch_quotes(self, symbols: Iterable[str]) -> list[MarketCapQuote]:
        ...

    async def fetch_usdkrw(self) -> Optional[float]:
        ...


class YahooUsMarketCapProvider:
    """Yahoo quote endpoint 기반 미국주식 시총 provider.

    공식 보장 API는 아니므로 서비스 테스트에서는 provider를 주입해 네트워크를 격리한다.
    """

    _QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

    def __init__(self, logger=None, timeout_sec: float = 10.0):
        self._logger = logger or logging.getLogger(__name__)
        self._timeout_sec = timeout_sec

    async def _fetch_raw(self, symbols: Iterable[str]) -> list[dict]:
        params = {
            "symbols": ",".join(str(symbol).upper() for symbol in symbols),
            "lang": "en-US",
            "region": "US",
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=self._timeout_sec, headers=headers) as client:
            response = await client.get(self._QUOTE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        result = payload.get("quoteResponse", {}).get("result", [])
        return result if isinstance(result, list) else []

    async def fetch_quotes(self, symbols: Iterable[str]) -> list[MarketCapQuote]:
        quotes: list[MarketCapQuote] = []
        try:
            rows = await self._fetch_raw(symbols)
        except Exception as exc:
            self._logger.error(f"미국 시총 조회 실패: {exc}", exc_info=True)
            return quotes

        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                symbol = str(row.get("symbol") or "").upper()
                market_cap = int(row.get("marketCap") or 0)
            except (TypeError, ValueError):
                continue
            if not symbol or market_cap <= 0:
                continue
            quotes.append(
                MarketCapQuote(
                    symbol=symbol,
                    name=str(row.get("shortName") or row.get("longName") or symbol),
                    currency=str(row.get("currency") or "USD"),
                    market_cap=market_cap,
                )
            )
        return quotes

    async def fetch_usdkrw(self) -> Optional[float]:
        try:
            rows = await self._fetch_raw(["KRW=X"])
        except Exception as exc:
            self._logger.error(f"USD/KRW 환율 조회 실패: {exc}", exc_info=True)
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                value = float(row.get("regularMarketPrice") or row.get("bid") or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None


class StaticUsMarketCapProvider:
    """테스트용 정적 provider."""

    def __init__(self, quotes: list[MarketCapQuote], usdkrw: Optional[float]):
        self._quotes = quotes
        self._usdkrw = usdkrw

    async def fetch_quotes(self, symbols: Iterable[str]) -> list[MarketCapQuote]:
        wanted = {str(symbol).upper() for symbol in symbols}
        return [quote for quote in self._quotes if quote.symbol.upper() in wanted]

    async def fetch_usdkrw(self) -> Optional[float]:
        return self._usdkrw


class MarketCapGapService:
    DEFAULT_KOREAN_TARGETS = (
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
    )
    DEFAULT_US_TARGETS = (
        "NVDA",
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AVGO",
        "TSLA",
        "MU",
        "SNDK",
    )

    def __init__(
        self,
        broker,
        us_provider: Optional[UsMarketCapProvider] = None,
        korean_targets: Iterable[tuple[str, str]] = DEFAULT_KOREAN_TARGETS,
        us_targets: Iterable[str] = DEFAULT_US_TARGETS,
        logger=None,
    ):
        self._broker = broker
        self._us_provider = us_provider or YahooUsMarketCapProvider(logger=logger)
        self._korean_targets = tuple(korean_targets)
        self._us_targets = tuple(str(symbol).upper() for symbol in us_targets)
        self._logger = logger or logging.getLogger(__name__)

    async def _fetch_korean_caps(self) -> list[dict]:
        items: list[dict] = []
        for code, name in self._korean_targets:
            try:
                resp = await self._broker.get_market_cap(code)
            except Exception as exc:
                self._logger.warning(f"국내 시총 조회 실패: {code} {exc}")
                continue
            if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
                self._logger.warning(f"국내 시총 응답 실패: {code} {getattr(resp, 'msg1', '')}")
                continue
            try:
                market_cap = int(getattr(resp, "data", 0) or 0)
            except (TypeError, ValueError):
                continue
            if market_cap <= 0:
                continue
            items.append({
                "symbol": code,
                "name": name,
                "currency": "KRW",
                "market_cap_krw": market_cap,
            })
        return items

    async def _fetch_us_caps(self, fx_rate: Optional[float]) -> list[dict]:
        quotes = await self._us_provider.fetch_quotes(self._us_targets)
        items: list[dict] = []
        for quote in quotes:
            if quote.market_cap <= 0:
                continue
            market_cap_krw = int(quote.market_cap * fx_rate) if fx_rate else None
            items.append({
                "symbol": quote.symbol.upper(),
                "name": quote.name,
                "currency": quote.currency,
                "market_cap_usd": quote.market_cap,
                "market_cap_krw": market_cap_krw,
            })
        items.sort(key=lambda item: item.get("market_cap_krw") or item.get("market_cap_usd") or 0, reverse=True)
        return items

    @staticmethod
    def _build_comparisons(korean: list[dict], us: list[dict]) -> list[dict]:
        comparisons: list[dict] = []
        for kr in korean:
            kr_cap = int(kr["market_cap_krw"])
            if kr_cap <= 0:
                continue
            for us_item in us:
                us_cap = us_item.get("market_cap_krw")
                if not us_cap:
                    continue
                gap = int(us_cap) - kr_cap
                comparisons.append({
                    "korean_symbol": kr["symbol"],
                    "korean_name": kr["name"],
                    "korean_market_cap_krw": kr_cap,
                    "us_symbol": us_item["symbol"],
                    "us_name": us_item["name"],
                    "us_market_cap_krw": int(us_cap),
                    "gap_krw": gap,
                    "ratio": round(int(us_cap) / kr_cap, 2),
                })
        comparisons.sort(key=lambda item: (item["korean_symbol"], -item["us_market_cap_krw"]))
        return comparisons

    async def build_report(self, report_date: str, trigger: str) -> dict:
        fx_rate = await self._us_provider.fetch_usdkrw()
        korean = await self._fetch_korean_caps()
        us = await self._fetch_us_caps(fx_rate)
        return {
            "report_date": report_date,
            "trigger": trigger,
            "fx_rate": fx_rate,
            "korean": korean,
            "us": us,
            "comparisons": self._build_comparisons(korean, us) if fx_rate else [],
        }
