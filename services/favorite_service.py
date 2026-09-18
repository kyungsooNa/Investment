"""
관심종목 서비스 - 비즈니스 로직 담당.
"""
import asyncio
from datetime import datetime

import pytz

from repositories.favorite_repository import (
    FavoriteRepository,
    MARKET_DOMESTIC,
    MARKET_OVERSEAS_US,
)
from repositories.stock_code_repository import StockCodeRepository

_DEFAULT_OVERSEAS_EXCHANGE = "NASD"
_KST = pytz.timezone("Asia/Seoul")


def _normalize_domestic_code(code: str) -> str:
    normalized = str(code or "").strip()
    if normalized.isdigit() and len(normalized) <= 6:
        return normalized.zfill(6)
    return normalized


def _normalize_overseas_symbol(code: str) -> str:
    return str(code or "").strip().upper()


def _extract_price_rate(data) -> tuple:
    """API 응답 dict 또는 dataclass에서 (stck_prpr, prdy_ctrt) 추출."""
    if isinstance(data, dict):
        output = data.get("output", data)
        if isinstance(output, dict):
            return output.get("stck_prpr"), output.get("prdy_ctrt")
        return getattr(output, "stck_prpr", None), getattr(output, "prdy_ctrt", None)
    return getattr(data, "stck_prpr", None), getattr(data, "prdy_ctrt", None)


def _apply_price_rate(entry: dict, data) -> bool:
    """추출한 (현재가, 등락률)을 entry에 반영하고 완전한 값인지 여부를 반환.

    현재가가 비어 있으면 반영하지 않는다(다음 단계에서 다시 조회).
    현재가만 있고 등락률이 없으면 값은 남기되 미완료로 보고, 이후 단계가
    현재가·등락률을 함께 가진 소스를 찾으면 통째로 덮어쓴다.
    """
    price, rate = _extract_price_rate(data)
    if price in (None, ""):
        return False
    entry["price"] = price
    entry["rate"] = rate
    return rate not in (None, "")


def _as_of_from_epoch(timestamp) -> str:
    """캐시 저장 시각(epoch)을 KST ISO8601 문자열로. 못 쓰는 값이면 None."""
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(timestamp), tz=_KST).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _as_of_from_trade_date(trade_date) -> str:
    """일봉 거래일(YYYYMMDD)을 YYYY-MM-DD 로. 형식이 다르면 None."""
    text = str(trade_date or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _snapshot_trade_date(snap) -> str:
    """일봉 스냅샷의 기준 거래일(YYYYMMDD)을 반환."""
    if not isinstance(snap, dict):
        return ""
    trade_date = snap.get("_trade_date")
    if trade_date:
        return str(trade_date)
    output = snap.get("output")
    if isinstance(output, dict):
        return str(output.get("stck_bsop_date") or "")
    return str(getattr(output, "stck_bsop_date", "") or "")


class FavoriteService:
    def __init__(
        self,
        repository: FavoriteRepository,
        stock_code_repository: StockCodeRepository,
        stock_query_service=None,
        stock_repository=None,
        rs_rating_service=None,
        overseas_stock_code_repository=None,
        market_calendar_service=None,
    ):
        self.repository = repository
        self.stock_code_repository = stock_code_repository
        self.stock_query_service = stock_query_service
        self.stock_repository = stock_repository
        self.rs_rating_service = rs_rating_service
        self.overseas_stock_code_repository = overseas_stock_code_repository
        self.market_calendar_service = market_calendar_service

    async def get_all(self, market: str = MARKET_DOMESTIC) -> list:
        codes = await self.repository.get_all(market=market)
        if market == MARKET_OVERSEAS_US:
            normalized_symbols = []
            seen = set()
            for code in codes:
                normalized = _normalize_overseas_symbol(code)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                normalized_symbols.append(normalized)
            return normalized_symbols
        if market != MARKET_DOMESTIC:
            return codes
        normalized_codes = []
        seen = set()
        for code in codes:
            normalized = _normalize_domestic_code(code)
            if normalized in seen:
                continue
            seen.add(normalized)
            normalized_codes.append(normalized)
        return normalized_codes

    async def add(self, code: str, market: str = MARKET_DOMESTIC) -> bool:
        if market == MARKET_DOMESTIC:
            code = _normalize_domestic_code(code)
        elif market == MARKET_OVERSEAS_US:
            code = _normalize_overseas_symbol(code)
        return await self.repository.add(code, market=market)

    async def remove(self, code: str, market: str = MARKET_DOMESTIC) -> bool:
        if market == MARKET_DOMESTIC:
            normalized = _normalize_domestic_code(code)
            removed = await self.repository.remove(normalized, market=market)
            if removed or normalized == str(code or "").strip():
                return removed
            return await self.repository.remove(str(code or "").strip(), market=market)
        if market == MARKET_OVERSEAS_US:
            normalized = _normalize_overseas_symbol(code)
            removed = await self.repository.remove(normalized, market=market)
            if removed or normalized == str(code or "").strip():
                return removed
            return await self.repository.remove(str(code or "").strip(), market=market)
        return await self.repository.remove(code, market=market)

    async def is_favorite(self, code: str, market: str = MARKET_DOMESTIC) -> bool:
        if market == MARKET_DOMESTIC:
            normalized = _normalize_domestic_code(code)
            if await self.repository.is_favorite(normalized, market=market):
                return True
            if normalized != str(code or "").strip():
                return await self.repository.is_favorite(str(code or "").strip(), market=market)
            return False
        if market == MARKET_OVERSEAS_US:
            normalized = _normalize_overseas_symbol(code)
            if await self.repository.is_favorite(normalized, market=market):
                return True
            if normalized != str(code or "").strip():
                return await self.repository.is_favorite(str(code or "").strip(), market=market)
            return False
        return await self.repository.is_favorite(code, market=market)

    async def get_with_details(self, market: str = MARKET_DOMESTIC) -> list:
        """관심종목 목록에 종목명·현재가·등락률을 포함하여 반환.

        조회 소스는 장 운영 여부에 따라 순서가 바뀐다.
        장중: StockQueryService(신선 WebSocket snapshot 우선, REST fallback)
              → 메모리 캐시 → DB 일봉 스냅샷
        장외: DB 일봉 스냅샷(최근 거래일) → StockQueryService → 메모리 캐시
        위 소스가 모두 비면 마지막으로 알고 있는 값을 신선도 검증 없이 쓰고
        price_stale/price_as_of 로 표시한다. 그것조차 없으면 종목명만 반환.
        """
        if market == MARKET_OVERSEAS_US:
            return await self._get_overseas_details()

        codes = await self.get_all(market=market)
        if not codes:
            return []

        # 종목명 일괄 조회
        result = {
            code: {
                "code": code,
                "name": self.stock_code_repository.get_name_by_code(code) or code,
                "price": None,
                "rate": None,
                "price_stale": False,
                "price_as_of": None,
                "rs_rating": None,
                "minervini_stage": None,
            }
            for code in codes
        }

        missing = list(codes)

        # 장이 닫혀 있으면 최근 거래일 일봉을 먼저 쓴다.
        # 장 시작 전·비거래일의 현재가 API 는 전일 종가에 등락률 0.00 을 얹어 돌려주므로,
        # 라이브 값을 그대로 쓰면 직전 세션 등락률이 뜨는 종목과 한 표에 섞인다.
        if await self._is_market_open():
            stages = (self._fill_from_query_service,
                      self._fill_from_memory_cache,
                      self._fill_from_daily_snapshot)
        else:
            stages = (self._fill_from_daily_snapshot,
                      self._fill_from_query_service,
                      self._fill_from_memory_cache)

        # 마지막으로 알고 있는 값은 장중·장외 공통 최종 단계다.
        for stage in stages + (self._fill_from_last_known,):
            if not missing:
                break
            missing = await stage(result, missing)

        # 4단계: RS Rating 점수 조회 및 병합
        if self.rs_rating_service:
            rs_tasks = [self.rs_rating_service.get_rating(c) for c in result.keys()]
            rs_responses = await asyncio.gather(*rs_tasks, return_exceptions=True)
            for code, rs_resp in zip(result.keys(), rs_responses):
                if not isinstance(rs_resp, Exception) and getattr(rs_resp, "rt_cd", None) == "0" and rs_resp.data:
                    result[code]["rs_rating"] = rs_resp.data.rs_rating

        # 5단계: Minervini Stage 조회 및 병합 (병렬, 3초 timeout)
        minervini_svc = getattr(self, "minervini_stage_service", None)
        if minervini_svc:
            async def _get_stage(code: str) -> int:
                try:
                    res = await asyncio.wait_for(
                        minervini_svc.get_stage_for_code(code), timeout=3.0
                    )
                    # get_stage_for_code now returns (stage, reason)
                    if isinstance(res, tuple) and len(res) >= 1:
                        return int(res[0]) if res[0] is not None else 0
                    return int(res) if res is not None else 0
                except Exception:
                    return 0

            stage_results = await asyncio.gather(
                *[_get_stage(c) for c in result.keys()], return_exceptions=True
            )
            for code, stage in zip(result.keys(), stage_results):
                if not isinstance(stage, Exception) and isinstance(stage, int) and stage > 0:
                    result[code]["minervini_stage"] = stage

        return list(result.values())

    async def _fill_from_query_service(self, result: dict, missing: list) -> list:
        """StockQueryService 현재가 조회 (종목당 5초 timeout)."""
        if not self.stock_query_service:
            return missing

        async def _fetch(code):
            try:
                return await asyncio.wait_for(
                    self.stock_query_service.get_current_price(
                        code, count_stats=False, caller="FavoriteService"
                    ),
                    timeout=5.0,
                )
            except Exception:
                return None

        still_missing = []
        responses = await asyncio.gather(*[_fetch(c) for c in missing])
        for code, resp in zip(missing, responses):
            if not (resp and resp.rt_cd == "0" and resp.data
                    and _apply_price_rate(result[code], resp.data)):
                still_missing.append(code)
        return still_missing

    async def _fill_from_memory_cache(self, result: dict, missing: list) -> list:
        """StockRepository 메모리 캐시 (서비스 미주입/조회 실패 시 graceful fallback)."""
        if not self.stock_repository:
            return missing

        still_missing = []
        for code in missing:
            cached = self.stock_repository.get_current_price(code, max_age_sec=3.0, count_stats=False)
            if not (cached and _apply_price_rate(result[code], cached)):
                still_missing.append(code)
        return still_missing

    async def _fill_from_daily_snapshot(self, result: dict, missing: list) -> list:
        """DB 일봉 스냅샷.

        최근 거래일보다 오래된 스냅샷은 현재가/등락률로 쓰지 않는다
        (MarketDataService.get_current_price 와 동일 기준).
        """
        if not self.stock_repository:
            return missing

        latest_trading_date = await self._get_latest_trading_date()
        still_missing = []
        snapshot_tasks = [self.stock_repository.get_latest_daily_snapshot(code) for code in missing]
        snapshots = await asyncio.gather(*snapshot_tasks, return_exceptions=True)
        for code, snap in zip(missing, snapshots):
            if isinstance(snap, Exception) or not snap:
                still_missing.append(code)
                continue
            if latest_trading_date and _snapshot_trade_date(snap) != latest_trading_date:
                still_missing.append(code)
                continue
            if not _apply_price_rate(result[code], snap):
                still_missing.append(code)
        return still_missing

    async def _fill_from_last_known(self, result: dict, missing: list) -> list:
        """마지막으로 알고 있는 값을 신선도 검증 없이 쓰고 stale 로 표시한다.

        장중에는 일봉 스냅샷이 항상 전일자라 거래일 검증에 걸려 버려지고, 메모리 캐시는
        WebSocket 구독 밖 종목이면 3초 TTL 에 걸린다. 그래서 REST 가 한 번 밀리면
        (429 백오프가 종목당 timeout 을 넘긴다) 화면에서 가격이 통째로 사라진다.
        기준 시각을 붙인 옛 값이 빈 칸보다 낫다.
        """
        if not self.stock_repository:
            return missing

        still_missing = []
        for code in missing:
            entry = result[code]
            if entry["price"] not in (None, ""):
                # 살아있는 소스가 가격은 채웠고 등락률만 비어 남은 경우 — stale 이 아니다.
                continue

            as_of = None
            cached = self.stock_repository.get_current_price(
                code, max_age_sec=float("inf"), count_stats=False
            )
            if cached:
                _apply_price_rate(entry, cached)
                if entry["price"] not in (None, ""):
                    as_of = _as_of_from_epoch(self._price_updated_at(code))

            if entry["price"] in (None, ""):
                snap = await self._last_daily_snapshot(code)
                if snap:
                    _apply_price_rate(entry, snap)
                    if entry["price"] not in (None, ""):
                        as_of = _as_of_from_trade_date(_snapshot_trade_date(snap))

            if entry["price"] in (None, ""):
                still_missing.append(code)
                continue

            entry["price_stale"] = True
            entry["price_as_of"] = as_of
        return still_missing

    def _price_updated_at(self, code: str):
        """캐시 저장 시각. 저장소가 제공하지 않으면 None (가격 자체는 그대로 쓴다)."""
        getter = getattr(self.stock_repository, "get_price_updated_at", None)
        if not callable(getter):
            return None
        try:
            return getter(code)
        except Exception:
            return None

    async def _last_daily_snapshot(self, code: str):
        """거래일 검증 없는 일봉 스냅샷 조회. 실패는 값 없음으로 취급한다."""
        try:
            return await self.stock_repository.get_latest_daily_snapshot(code)
        except Exception:
            return None

    async def _is_market_open(self) -> bool:
        """장 운영 중 여부. 캘린더 서비스가 없거나 실패하면 True (라이브 우선, 기존 동작)."""
        if not self.market_calendar_service:
            return True
        try:
            return bool(await self.market_calendar_service.is_market_open_now())
        except Exception:
            return True

    async def _get_latest_trading_date(self):
        """최근 거래일(YYYYMMDD). 캘린더 서비스가 없거나 실패하면 None (검증 생략)."""
        if not self.market_calendar_service:
            return None
        try:
            return await self.market_calendar_service.get_latest_trading_date()
        except Exception:
            return None

    async def _get_overseas_details(self) -> list:
        """미국장 관심종목에 종목명·거래소·현재가·등락률을 붙여 반환.

        해외는 실시간 스트림/일봉 스냅샷 경로가 없어 해외 현재가 API만 사용한다.
        RS Rating·Minervini Stage는 국내 데이터 기반이라 항상 None이다.
        """
        symbols = await self.get_all(market=MARKET_OVERSEAS_US)
        if not symbols:
            return []

        result = {}
        for symbol in symbols:
            meta = None
            if self.overseas_stock_code_repository:
                meta = self.overseas_stock_code_repository.get_meta(symbol)
            result[symbol] = {
                "code": symbol,
                "name": (meta or {}).get("name") or symbol,
                "exchange": (meta or {}).get("exchange") or _DEFAULT_OVERSEAS_EXCHANGE,
                "price": None,
                "rate": None,
                "rs_rating": None,
                "minervini_stage": None,
            }

        if not self.stock_query_service:
            return list(result.values())

        async def _fetch(symbol):
            try:
                return await asyncio.wait_for(
                    self.stock_query_service.get_overseas_price(
                        symbol, exchange=result[symbol]["exchange"]
                    ),
                    timeout=5.0,
                )
            except Exception:
                return None

        responses = await asyncio.gather(*[_fetch(s) for s in symbols])
        for symbol, resp in zip(symbols, responses):
            if resp and resp.rt_cd == "0" and resp.data:
                result[symbol]["price"] = getattr(resp.data, "price", None)
                result[symbol]["rate"] = getattr(resp.data, "change_rate", None)

        return list(result.values())
