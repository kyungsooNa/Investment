"""
관심종목 서비스 - 비즈니스 로직 담당.
"""
import asyncio
from repositories.favorite_repository import FavoriteRepository
from repositories.stock_code_repository import StockCodeRepository


def _extract_price_rate(data) -> tuple:
    """API 응답 dict 또는 dataclass에서 (stck_prpr, prdy_ctrt) 추출."""
    if isinstance(data, dict):
        output = data.get("output", data)
        if isinstance(output, dict):
            return output.get("stck_prpr"), output.get("prdy_ctrt")
        return getattr(output, "stck_prpr", None), getattr(output, "prdy_ctrt", None)
    return getattr(data, "stck_prpr", None), getattr(data, "prdy_ctrt", None)


class FavoriteService:
    def __init__(
        self,
        repository: FavoriteRepository,
        stock_code_repository: StockCodeRepository,
        stock_query_service=None,
        stock_repository=None,
    ):
        self.repository = repository
        self.stock_code_repository = stock_code_repository
        self.stock_query_service = stock_query_service
        self.stock_repository = stock_repository

    def get_all(self) -> list:
        return self.repository.get_all()

    def add(self, code: str) -> bool:
        return self.repository.add(code)

    def remove(self, code: str) -> bool:
        return self.repository.remove(code)

    def is_favorite(self, code: str) -> bool:
        return self.repository.is_favorite(code)

    async def get_with_details(self) -> list:
        """관심종목 목록에 종목명·현재가·등락률을 포함하여 반환.

        1단계: StockRepository 메모리 캐시 (stock_price_repository, 즉시)
        2단계: StockRepository DB 스냅샷 (장마감 후 일봉 데이터)
        3단계: 개별 current_price API 호출 (5초 timeout, 실전/모의 공통)
        stock_query_service 없으면 종목명만 반환 (graceful degradation).
        """
        codes = self.repository.get_all()
        if not codes:
            return []

        # 종목명 일괄 조회
        result = {
            code: {
                "code": code,
                "name": self.stock_code_repository.get_name_by_code(code) or code,
                "price": None,
                "rate": None,
            }
            for code in codes
        }

        missing = list(codes)

        # 1단계: 메모리 캐시 (TTL 무제한 — 장마감 후 마지막 틱도 활용)
        if self.stock_repository:
            still_missing = []
            for code in missing:
                cached = self.stock_repository.get_current_price(code, max_age_sec=float("inf"), count_stats=False)
                if cached:
                    price, rate = _extract_price_rate(cached)
                    result[code]["price"] = price
                    result[code]["rate"] = rate
                else:
                    still_missing.append(code)
            missing = still_missing

        # 2단계: DB 스냅샷 (장마감 후 일봉)
        if missing and self.stock_repository:
            still_missing = []
            snapshot_tasks = [self.stock_repository.get_latest_daily_snapshot(code) for code in missing]
            snapshots = await asyncio.gather(*snapshot_tasks, return_exceptions=True)
            for code, snap in zip(missing, snapshots):
                if isinstance(snap, Exception) or not snap:
                    still_missing.append(code)
                    continue
                price, rate = _extract_price_rate(snap)
                result[code]["price"] = price
                result[code]["rate"] = rate
            missing = still_missing

        # 3단계: 개별 API 호출 (5초 timeout)
        if missing and self.stock_query_service:
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

            responses = await asyncio.gather(*[_fetch(c) for c in missing])
            for code, resp in zip(missing, responses):
                if resp and resp.rt_cd == "0" and resp.data:
                    price, rate = _extract_price_rate(resp.data)
                    result[code]["price"] = price
                    result[code]["rate"] = rate

        return list(result.values())
