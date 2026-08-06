"""장 마감 우량주·국내 즐겨찾기 대상의 AI 보조 의견 생성."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.ai_signal import extract_signal


class PremiumWatchlistAiAnalysisService:
    """정량 선정 결과는 유지한 채, 제한된 종목에 AI 의견을 덧붙인다."""

    def __init__(
        self,
        *,
        ai_stock_analyzer,
        favorite_service,
        stock_code_repository,
        stock_query_service=None,
        minervini_stage_service=None,
        rs_rating_service=None,
        dart_disclosure_repository=None,
        stock_news_collector=None,
        logger=None,
        max_stocks: int = 30,
    ) -> None:
        self._analyzer = ai_stock_analyzer
        self._favorites = favorite_service
        self._stock_codes = stock_code_repository
        self._stock_query = stock_query_service
        self._stage_service = minervini_stage_service
        self._rs_rating_service = rs_rating_service
        self._disclosures = dart_disclosure_repository
        self._news = stock_news_collector
        self._logger = logger or logging.getLogger(__name__)
        self._max_stocks = max(1, int(max_stocks))

    async def analyze(self, *, kospi: list[dict], kosdaq: list[dict], report_date: str) -> dict[str, dict]:
        """우량주 우선으로 즐겨찾기를 합쳐 중복 없이 최대 N종목을 분석한다."""
        candidates: list[tuple[str, str, str, dict]] = []
        seen: set[str] = set()
        for stock in [*kospi, *kosdaq]:
            code = str(stock.get("code") or "").strip().zfill(6)
            if not code or code in seen:
                continue
            seen.add(code)
            candidates.append((code, str(stock.get("name") or code), "premium", stock))

        favorite_codes = await self._favorites.get_all() if self._favorites else []
        for raw_code in favorite_codes:
            code = str(raw_code or "").strip().zfill(6)
            if not code or code in seen:
                continue
            seen.add(code)
            name = self._stock_codes.get_name_by_code(code) if self._stock_codes else None
            candidates.append((code, str(name or code), "favorite", {}))

        results: dict[str, dict] = {}
        for code, name, source, stock in candidates[:self._max_stocks]:
            context = await self._build_context(code, name, source, stock, report_date)
            try:
                analysis = await self._analyzer.analyze(context)
                signal, signal_reason, _ = extract_signal(analysis)
                results[code] = {
                    "name": name, "source": source,
                    "signal": signal, "signal_reason": signal_reason,
                }
            except Exception as exc:
                self._logger.warning("장 마감 AI 분석 실패 (%s): %s", code, exc)
        return results

    async def _build_context(self, code: str, name: str, source: str, stock: dict, report_date: str) -> dict:
        current, financial, stage, rs_rating, investor_flow, disclosures, news = await asyncio.gather(
            self._optional_current_price(code),
            self._optional_call(self._stock_query, "get_financial_ratio", code),
            self._optional_call(self._stage_service, "get_stage_for_code", code),
            self._optional_call(self._rs_rating_service, "get_rating", code),
            self._optional_call(self._stock_query, "get_investor_trade_daily_multi", code, days=5),
            self._optional_call(self._disclosures, "get_recent_by_stock_code", code, limit=5),
            self._optional_call(self._news, "collect", code, limit=10),
        )
        return {
            "code": code, "name": name, "report_date": report_date,
            "candidate_source": "전일 우량주" if source == "premium" else "즐겨찾기",
            "premium_metrics": stock or None,
            "current": self._response_data(current),
            "financial": self._response_data(financial),
            "stage": self._response_data(stage),
            "rs_rating": self._response_data(rs_rating),
            "investor_flow": self._response_data(investor_flow),
            "disclosures": self._response_data(disclosures),
            "news": self._response_data(news),
        }

    async def _optional_current_price(self, code: str) -> Any:
        if not self._stock_query:
            return None
        method = getattr(self._stock_query, "handle_get_current_stock_price", None)
        if method is None:
            return None
        try:
            return await method(code, caller="PremiumWatchlistAiAnalysisService", force_fresh=True)
        except Exception:
            return None

    @staticmethod
    async def _optional_call(target, method_name: str, *args, **kwargs) -> Any:
        method = getattr(target, method_name, None) if target is not None else None
        if method is None:
            return None
        try:
            return await method(*args, **kwargs)
        except Exception:
            return None

    @staticmethod
    def _response_data(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple)):
            return value
        if getattr(value, "rt_cd", None) == "0":
            return getattr(value, "data", None)
        return value
