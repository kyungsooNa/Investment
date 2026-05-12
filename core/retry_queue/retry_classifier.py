# core/retry_queue/retry_classifier.py
from enum import Enum
from common.types import ResCommonResponse, ErrorCode


class RequestOutcome(Enum):
    DONE  = "done"   # 성공
    RETRY = "retry"  # 재시도 가능 실패
    FAIL  = "fail"   # 최종 실패 (재시도 불가)


# KIS API msg1에 포함될 경우 재시도가 무의미한 비즈니스 오류 키워드
_NON_RETRIABLE_MSG_PATTERNS = [
    "잔고부족", "주문가능금액", "거래정지", "주문불가", "상장폐지",
    "매도가능수량", "이미처리", "접수불가", "매매불가능종목",
    "종목코드 오류", "유효하지 않은", "입력값 오류", "주문이 불가",
]

# KIS API msg1에 포함될 경우 일시적 과부하로 재시도 가능한 키워드
_RETRIABLE_MSG_PATTERNS = [
    "초당 거래건수", "분당 거래건수", "잠시 후", "서버 과부하",
    "too many", "rate limit", "요청이 많습니다",
]

# 재시도 불가 ErrorCode (비즈니스 오류, 입력 오류)
_NON_RETRIABLE_CODES = frozenset({
    ErrorCode.MARKET_CLOSED,
    ErrorCode.INVALID_INPUT,
    ErrorCode.MISSING_KEY,
    ErrorCode.EMPTY_VALUES,
    ErrorCode.WRONG_RET_TYPE,
})


def classify(result: ResCommonResponse | None) -> RequestOutcome:
    """
    API 응답을 분석하여 요청 결과를 분류합니다.
    - DONE : 성공
    - RETRY: 일시적 오류 (네트워크, 과부하 등) → 재시도 가능
    - FAIL : 비즈니스 오류 (잔고부족, 주문불가 등) → 재시도 무의미
    """
    if result is None:
        # 응답 자체가 없으면 네트워크 레벨 문제 → 재시도
        return RequestOutcome.RETRY

    if result.rt_cd == ErrorCode.SUCCESS.value:
        return RequestOutcome.DONE

    try:
        code = ErrorCode(result.rt_cd)
    except ValueError:
        # 우리 ErrorCode 매핑에 없는 KIS 내부 코드 → msg1으로 판단
        return _classify_by_msg(result.msg1)

    if code in _NON_RETRIABLE_CODES:
        return RequestOutcome.FAIL

    if code.is_retriable:  # NETWORK_ERROR, RETRY_LIMIT
        return RequestOutcome.RETRY

    # API_ERROR(100), PARSING_ERROR(101), UNKNOWN_ERROR(999) 등
    # → msg1 키워드로 세분화
    return _classify_by_msg(result.msg1)


def _classify_by_msg(msg: str | None) -> RequestOutcome:
    if not msg:
        return RequestOutcome.RETRY  # 메시지 없음 → 일단 재시도

    if any(kw in msg for kw in _NON_RETRIABLE_MSG_PATTERNS):
        return RequestOutcome.FAIL

    if any(kw in msg for kw in _RETRIABLE_MSG_PATTERNS):
        return RequestOutcome.RETRY

    # 알 수 없는 오류 → 안전하게 FAIL (재시도해도 같은 결과일 가능성 높음)
    return RequestOutcome.FAIL


def is_non_retriable_business_error(result: ResCommonResponse | None) -> bool:
    """계좌/잔고/종목 상태처럼 재시도해도 성공 가능성이 낮은 주문 거부인지 판정."""
    if result is None or result.rt_cd == ErrorCode.SUCCESS.value:
        return False

    msg = result.msg1 or ""
    if "Business Error" in msg:
        return True
    if any(kw in msg for kw in _NON_RETRIABLE_MSG_PATTERNS):
        return True

    try:
        code = ErrorCode(result.rt_cd)
    except ValueError:
        return False
    return code in _NON_RETRIABLE_CODES
