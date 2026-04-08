"""
관심종목 서비스 - 비즈니스 로직 담당.
"""
import asyncio
from repositories.favorite_repository import FavoriteRepository
from repositories.stock_code_repository import StockCodeRepository


class FavoriteService:
    def __init__(
        self,
        repository: FavoriteRepository,
        stock_code_repository: StockCodeRepository,
        stock_query_service=None,
    ):
        self.repository = repository
        self.stock_code_repository = stock_code_repository
        self.stock_query_service = stock_query_service

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

        종목명: stock_code_repository (메모리 캐시, 동기)
        현재가: stock_query_service.get_multi_price (비동기 1회 일괄 조회) — N+1 방지
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

        # 현재가 일괄 조회 (1회 API 호출)
        if self.stock_query_service:
            try:
                resp = await self.stock_query_service.get_multi_price(codes)
                if resp and resp.rt_cd == "0" and resp.data:
                    for item in resp.data:
                        code = item.get("stck_shrn_iscd", "")
                        if code in result:
                            result[code]["price"] = item.get("stck_prpr")
                            result[code]["rate"] = item.get("prdy_ctrt")
            except Exception:
                pass

        return list(result.values())
