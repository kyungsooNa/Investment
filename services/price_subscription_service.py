# services/price_subscription_service.py
"""
하위 호환성 유지를 위한 re-export 모듈.

실제 구현은 services/subscription_policy.py 의 SubscriptionPolicy 클래스에 있습니다.
기존 임포트 경로(from services.price_subscription_service import ...)는 그대로 동작합니다.
"""
from services.subscription_policy import SubscriptionPolicy, SubscriptionPriority

PriceSubscriptionService = SubscriptionPolicy

__all__ = ["PriceSubscriptionService", "SubscriptionPolicy", "SubscriptionPriority"]
