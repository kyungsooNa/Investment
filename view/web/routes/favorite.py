"""
관심종목 API 엔드포인트 (favorite.html).
"""
import asyncio
from fastapi import APIRouter
from view.web.api_common import _get_ctx

router = APIRouter(prefix="/favorite", tags=["favorite"])


def _ctx_attr(ctx, name: str, default=None):
    """MagicMock 테스트 컨텍스트에서 미정의 속성이 truthy Mock으로 생기는 것을 피한다."""
    if hasattr(ctx, "__dict__"):
        return ctx.__dict__.get(name, default)
    return getattr(ctx, name, default)


@router.get("")
async def get_favorite_list():
    """관심종목 목록 (종목명·현재가·등락률 포함) 반환."""
    ctx = _get_ctx()
    result = await ctx.favorite_service.get_with_details()

    # 페이지 접속 시 LOW 우선순위로 SSE 구독 등록 (백그라운드)
    if getattr(ctx, "price_subscription_service", None) and result:
        async def _subscribe():
            try:
                from services.price_subscription_service import SubscriptionPriority
                codes = [item["code"] for item in result]
                await ctx.price_subscription_service.sync_subscriptions(
                    codes=codes,
                    category_key="favorite",
                    priority=SubscriptionPriority.LOW,
                )
            except Exception as e:
                ctx.logger.warning(f"관심종목 구독 등록 실패: {e}")

        asyncio.create_task(_subscribe())

    return result


@router.post("/{code}")
async def add_favorite(code: str):
    """관심종목 추가."""
    ctx = _get_ctx()
    added = await ctx.favorite_service.add(code)
    if added:
        alert_service = _ctx_attr(ctx, "favorite_price_alert_service")
        if alert_service:
            await alert_service.add_favorite(code)
        price_subscription_service = _ctx_attr(ctx, "price_subscription_service")
        if price_subscription_service:
            try:
                from services.price_subscription_service import SubscriptionPriority
                await price_subscription_service.add_subscription(
                    code,
                    SubscriptionPriority.LOW,
                    "favorite",
                )
            except Exception as e:
                ctx.logger.warning(f"관심종목 구독 추가 실패: {e}")
    return {"success": True, "added": added, "code": code}


@router.delete("/{code}")
async def remove_favorite(code: str):
    """관심종목 제거."""
    ctx = _get_ctx()
    removed = await ctx.favorite_service.remove(code)
    if removed:
        alert_service = _ctx_attr(ctx, "favorite_price_alert_service")
        if alert_service:
            await alert_service.remove_favorite(code)
        price_subscription_service = _ctx_attr(ctx, "price_subscription_service")
        if price_subscription_service:
            try:
                await price_subscription_service.remove_subscription(code, "favorite")
            except Exception as e:
                ctx.logger.warning(f"관심종목 구독 제거 실패: {e}")
    return {"success": True, "removed": removed, "code": code}


@router.get("/{code}/status")
async def get_favorite_status(code: str):
    """특정 종목의 관심종목 등록 여부 확인 (stock.js 버튼 초기 상태용)."""
    ctx = _get_ctx()
    return {"code": code, "is_favorite": await ctx.favorite_service.is_favorite(code)}
