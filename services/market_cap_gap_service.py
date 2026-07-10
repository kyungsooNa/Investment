from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Protocol

import httpx

from common.types import ErrorCode

_DEFAULT_KOREAN_PROVIDER = object()


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


class KoreanMarketCapProvider(Protocol):
    async def fetch_market_caps(self, codes: Iterable[str], report_date: str) -> dict[str, int]:
        ...


class PykrxKoreanMarketCapProvider:
    """pykrx 기반 국내 시총 fallback provider.

    KIS 모의투자 환경에서는 일부 시총 API가 "없는 서비스 코드"로 실패할 수 있어
    장마감 리포트 전용 fallback으로 사용한다.
    """

    def __init__(self, logger=None, lookback_days: int = 7, db_path: str | Path = "data/stocks.db"):
        self._logger = logger or logging.getLogger(__name__)
        self._lookback_days = lookback_days
        self._db_path = Path(db_path)

    async def fetch_market_caps(self, codes: Iterable[str], report_date: str) -> dict[str, int]:
        wanted = [str(code).zfill(6) for code in codes]
        if not wanted:
            return {}
        return await asyncio.to_thread(self._fetch_sync, wanted, report_date)

    def _fetch_sync(self, codes: list[str], report_date: str) -> dict[str, int]:
        found = self._fetch_local_db(codes, report_date)
        if len(found) == len(codes):
            return found

        try:
            from pykrx import stock
        except Exception as exc:
            self._logger.warning(f"pykrx 국내 시총 fallback import 실패: {exc}")
            return found

        try:
            base_date = datetime.strptime(str(report_date), "%Y%m%d")
        except ValueError:
            base_date = datetime.now()

        missing_codes = [code for code in codes if code not in found]
        for offset in range(max(self._lookback_days, 0) + 1):
            date = (base_date - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = stock.get_market_cap_by_ticker(date, market="ALL")
            except Exception as exc:
                self._logger.warning(f"pykrx 국내 시총 조회 실패: {date} {exc}")
                continue
            if df is None or getattr(df, "empty", True):
                continue

            for code in missing_codes:
                if code in found or code not in df.index:
                    continue
                try:
                    value = int(df.loc[code].get("시가총액") or 0)
                except (TypeError, ValueError, AttributeError):
                    continue
                if value > 0:
                    found[code] = value
            if len(found) == len(codes):
                break
        return found

    def _fetch_local_db(self, codes: list[str], report_date: str) -> dict[str, int]:
        if not self._db_path.exists():
            return {}
        placeholders = ",".join("?" for _ in codes)
        query = f"""
            SELECT code, market_cap, trade_date
            FROM daily_prices
            WHERE code IN ({placeholders})
              AND trade_date <= ?
              AND market_cap IS NOT NULL
              AND market_cap > 0
            ORDER BY trade_date DESC
        """
        found: dict[str, int] = {}
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(query, [*codes, str(report_date)]).fetchall()
        except Exception as exc:
            self._logger.warning(f"로컬 국내 시총 fallback 조회 실패: {exc}")
            return found

        for code, raw_cap, _trade_date in rows:
            if code in found:
                continue
            try:
                cap = int(raw_cap or 0)
            except (TypeError, ValueError):
                continue
            if cap <= 0:
                continue
            if cap < 1_000_000_000:
                cap *= 100_000_000
            found[str(code).zfill(6)] = cap
        return found


class YahooUsMarketCapProvider:
    """Yahoo quote endpoint 기반 미국주식 시총 provider.

    공식 보장 API는 아니므로 서비스 테스트에서는 provider를 주입해 네트워크를 격리한다.
    """

    _QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
    _COOKIE_URL = "https://fc.yahoo.com"
    _CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
    _CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

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
        async with httpx.AsyncClient(timeout=self._timeout_sec, headers=headers, follow_redirects=True) as client:
            response = await client.get(self._QUOTE_URL, params=params)
            if response.status_code in (401, 403):
                crumb = await self._fetch_crumb(client)
                if crumb:
                    params = dict(params)
                    params["crumb"] = crumb
                    response = await client.get(self._QUOTE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        result = payload.get("quoteResponse", {}).get("result", [])
        return result if isinstance(result, list) else []

    async def _fetch_crumb(self, client: httpx.AsyncClient) -> Optional[str]:
        try:
            await client.get(self._COOKIE_URL)
            response = await client.get(self._CRUMB_URL)
        except Exception as exc:
            self._logger.warning(f"Yahoo crumb 조회 실패: {exc}")
            return None
        if response.status_code != 200:
            return None
        crumb = response.text.strip()
        return crumb or None

    async def _fetch_chart_price(self, symbol: str) -> Optional[float]:
        headers = {"User-Agent": "Mozilla/5.0"}
        url = self._CHART_URL.format(symbol=str(symbol).upper())
        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec, headers=headers, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            self._logger.warning(f"Yahoo chart 가격 조회 실패: {symbol} {exc}")
            return None
        results = payload.get("chart", {}).get("result", [])
        if not results:
            return None
        meta = results[0].get("meta") or {}
        try:
            value = float(meta.get("regularMarketPrice") or meta.get("previousClose") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

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
            return await self._fetch_chart_price("KRW=X")
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                value = float(row.get("regularMarketPrice") or row.get("bid") or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return await self._fetch_chart_price("KRW=X")


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
        korean_provider=_DEFAULT_KOREAN_PROVIDER,
        korean_targets: Iterable[tuple[str, str]] = DEFAULT_KOREAN_TARGETS,
        us_targets: Iterable[str] = DEFAULT_US_TARGETS,
        logger=None,
    ):
        self._broker = broker
        self._us_provider = us_provider or YahooUsMarketCapProvider(logger=logger)
        self._korean_provider = (
            PykrxKoreanMarketCapProvider(logger=logger)
            if korean_provider is _DEFAULT_KOREAN_PROVIDER
            else korean_provider
        )
        self._korean_targets = tuple(korean_targets)
        self._us_targets = tuple(str(symbol).upper() for symbol in us_targets)
        self._logger = logger or logging.getLogger(__name__)

    async def _fetch_korean_caps(self, report_date: str) -> list[dict]:
        items: list[dict] = []
        missing: list[tuple[str, str]] = []
        for code, name in self._korean_targets:
            try:
                resp = await self._broker.get_market_cap(code)
            except Exception as exc:
                self._logger.warning(f"국내 시총 조회 실패: {code} {exc}")
                missing.append((code, name))
                continue
            if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
                self._logger.warning(f"국내 시총 응답 실패: {code} {getattr(resp, 'msg1', '')}")
                missing.append((code, name))
                continue
            try:
                market_cap = int(getattr(resp, "data", 0) or 0)
            except (TypeError, ValueError):
                missing.append((code, name))
                continue
            if market_cap <= 0:
                missing.append((code, name))
                continue
            items.append({
                "symbol": code,
                "name": name,
                "currency": "KRW",
                "market_cap_krw": market_cap,
            })
        if missing and self._korean_provider:
            fallback_caps = await self._korean_provider.fetch_market_caps(
                [code for code, _ in missing],
                report_date,
            )
            for code, name in missing:
                market_cap = int(fallback_caps.get(code) or 0)
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

    async def get_us_market_caps(self) -> dict:
        """미국 대형주 시총 화면용 요약. 국내 시총 API는 호출하지 않는다."""
        fx_rate = await self._us_provider.fetch_usdkrw()
        return {
            "fx_rate": fx_rate,
            "items": await self._fetch_us_caps(fx_rate),
        }

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
        korean = await self._fetch_korean_caps(report_date)
        us = await self._fetch_us_caps(fx_rate)
        return {
            "report_date": report_date,
            "trigger": trigger,
            "fx_rate": fx_rate,
            "korean": korean,
            "us": us,
            "comparisons": self._build_comparisons(korean, us) if fx_rate else [],
        }
