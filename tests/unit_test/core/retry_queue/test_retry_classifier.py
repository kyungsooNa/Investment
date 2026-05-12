# tests/unit_test/test_retry_classifier.py

import pytest
from common.types import ResCommonResponse, ErrorCode
from core.retry_queue.retry_classifier import (
    classify,
    is_non_retriable_business_error,
    RequestOutcome,
    _classify_by_msg,
)


def resp(rt_cd: str, msg1: str = "") -> ResCommonResponse:
    return ResCommonResponse(rt_cd=rt_cd, msg1=msg1, data=None)


class TestClassifyNone:
    def test_none_result_returns_retry(self):
        """응답 자체가 없으면 네트워크 문제로 간주 → RETRY"""
        assert classify(None) == RequestOutcome.RETRY


class TestClassifySuccess:
    def test_success_returns_done(self):
        assert classify(resp(ErrorCode.SUCCESS.value, "정상")) == RequestOutcome.DONE


class TestClassifyNonRetriableErrorCodes:
    @pytest.mark.parametrize("code", [
        ErrorCode.MARKET_CLOSED,
        ErrorCode.INVALID_INPUT,
        ErrorCode.MISSING_KEY,
        ErrorCode.EMPTY_VALUES,
        ErrorCode.WRONG_RET_TYPE,
    ])
    def test_non_retriable_error_code_returns_fail(self, code):
        """비즈니스 오류 코드는 재시도 없이 즉시 FAIL"""
        assert classify(resp(code.value)) == RequestOutcome.FAIL


class TestClassifyRetriableErrorCodes:
    @pytest.mark.parametrize("code", [
        ErrorCode.NETWORK_ERROR,
        ErrorCode.RETRY_LIMIT,
    ])
    def test_retriable_error_code_returns_retry(self, code):
        """네트워크/과부하 오류는 재시도 가능 → RETRY"""
        assert classify(resp(code.value)) == RequestOutcome.RETRY


class TestClassifyByMsgKeyword:
    @pytest.mark.parametrize("msg", [
        "잔고부족",
        "주문가능금액",
        "거래정지",
        "주문불가",
        "매도가능수량",
        "이미처리",
        "접수불가",
        "매매불가능종목",
        "종목코드 오류",
        "유효하지 않은 코드",
        "입력값 오류",
    ])
    def test_non_retriable_msg_keyword_returns_fail(self, msg):
        """비즈니스 오류 키워드가 포함된 msg1 → FAIL"""
        # API_ERROR: NON_RETRIABLE_CODES에 없고, is_retriable=False → _classify_by_msg 호출
        assert classify(resp(ErrorCode.API_ERROR.value, msg)) == RequestOutcome.FAIL

    @pytest.mark.parametrize("msg", [
        "초당 거래건수 초과",
        "분당 거래건수 초과",
        "잠시 후 다시 시도해주세요",
        "서버 과부하 상태입니다",
        "too many requests",
        "rate limit exceeded",
        "요청이 많습니다",
    ])
    def test_retriable_msg_keyword_returns_retry(self, msg):
        """일시적 과부하 키워드가 포함된 msg1 → RETRY"""
        assert classify(resp(ErrorCode.API_ERROR.value, msg)) == RequestOutcome.RETRY

    def test_empty_msg_returns_retry(self):
        """msg1이 빈 문자열이면 일단 재시도"""
        assert classify(resp(ErrorCode.API_ERROR.value, "")) == RequestOutcome.RETRY

    def test_none_msg_returns_retry(self):
        """msg1이 None이면 일단 재시도 (_classify_by_msg 직접 검증)"""
        assert _classify_by_msg(None) == RequestOutcome.RETRY

    def test_unknown_msg_defaults_to_fail(self):
        """어떤 키워드에도 매칭되지 않으면 안전하게 FAIL (재시도해도 같은 결과 가능성 높음)"""
        assert classify(resp(ErrorCode.API_ERROR.value, "알 수 없는 오류 발생")) == RequestOutcome.FAIL

    def test_paper_account_reject_returns_fail(self):
        """KIS 모의투자 계좌 주문 불가 응답은 재시도 불가 비즈니스 거부로 고정합니다."""
        result = resp(
            ErrorCode.API_ERROR.value,
            "API 오류: Business Error: 모의투자 주문이 불가한 계좌입니다.",
        )

        assert classify(result) == RequestOutcome.FAIL
        assert is_non_retriable_business_error(result) is True


class TestClassifyUnknownErrorCode:
    """우리 ErrorCode enum에 없는 KIS 내부 코드 처리"""

    def test_unknown_rt_cd_with_retriable_msg_returns_retry(self):
        assert classify(resp("999999", "초당 거래건수 초과")) == RequestOutcome.RETRY

    def test_unknown_rt_cd_with_non_retriable_msg_returns_fail(self):
        assert classify(resp("999999", "잔고부족")) == RequestOutcome.FAIL

    def test_unknown_rt_cd_with_empty_msg_returns_retry(self):
        """코드도 모르고 메시지도 없으면 → RETRY (기회를 한 번 더 줌)"""
        assert classify(resp("999999", "")) == RequestOutcome.RETRY
