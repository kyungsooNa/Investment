"""해외 포지션 reconcile 서비스 테스트 (Phase 5).

로컬 기대 포지션 vs 브로커 해외 잔고 drift 감지(순수 비교, 주문 경로 없음).
브로커 잔고 응답은 표본별 키가 갈리므로 다중 후보 키로 관용 파싱한다.
"""
from common.types import ErrorCode, ResCommonResponse
from services.overseas_reconcile_service import OverseasReconcileService


def _balance(holdings: list[dict]) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
        data={"output1": holdings, "output2": {}},
    )


# ── parse_broker_positions ────────────────────────────────────────────────────

def test_parse_positions_primary_keys():
    resp = _balance([
        {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"},
        {"ovrs_pdno": "MSFT", "ovrs_cblc_qty": "5"},
    ])
    assert OverseasReconcileService.parse_broker_positions(resp) == {"AAPL": 10, "MSFT": 5}


def test_parse_positions_fallback_keys_and_uppercase():
    resp = _balance([
        {"pdno": "tsla", "cblc_qty": "3"},   # fallback 키 + 소문자 심볼
    ])
    assert OverseasReconcileService.parse_broker_positions(resp) == {"TSLA": 3}


def test_parse_positions_skips_zero_and_blank():
    resp = _balance([
        {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "0"},   # 0 수량 제외
        {"ovrs_pdno": "", "ovrs_cblc_qty": "5"},        # 빈 심볼 제외
        {"ovrs_pdno": "NVDA", "ovrs_cblc_qty": "bad"},  # 파싱 불가 → 0 → 제외
    ])
    assert OverseasReconcileService.parse_broker_positions(resp) == {}


def test_parse_positions_failed_response_returns_empty():
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None)
    assert OverseasReconcileService.parse_broker_positions(resp) == {}


# ── reconcile ─────────────────────────────────────────────────────────────────

def test_reconcile_all_matched_ok():
    svc = OverseasReconcileService()
    resp = _balance([{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}])
    report = svc.reconcile({"AAPL": 10}, resp)
    assert report["ok"] is True
    assert report["matched"] == ["AAPL"]
    assert report["missing_in_broker"] == []
    assert report["extra_in_broker"] == []
    assert report["qty_mismatch"] == []


def test_reconcile_missing_in_broker():
    svc = OverseasReconcileService()
    resp = _balance([])  # 브로커에 없음
    report = svc.reconcile({"AAPL": 10}, resp)
    assert report["ok"] is False
    assert report["missing_in_broker"] == [{"symbol": "AAPL", "local_qty": 10}]


def test_reconcile_extra_in_broker():
    svc = OverseasReconcileService()
    resp = _balance([{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"},
                     {"ovrs_pdno": "MSFT", "ovrs_cblc_qty": "5"}])
    report = svc.reconcile({"AAPL": 10}, resp)
    assert report["ok"] is False
    assert report["extra_in_broker"] == [{"symbol": "MSFT", "broker_qty": 5}]


def test_reconcile_qty_mismatch():
    svc = OverseasReconcileService()
    resp = _balance([{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "8"}])
    report = svc.reconcile({"AAPL": 10}, resp)
    assert report["ok"] is False
    assert report["qty_mismatch"] == [{"symbol": "AAPL", "local_qty": 10, "broker_qty": 8}]


def test_reconcile_failed_balance_is_not_ok():
    svc = OverseasReconcileService()
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="잔고조회실패", data=None)
    report = svc.reconcile({"AAPL": 10}, resp)
    assert report["ok"] is False
    assert report["error"] == "balance_query_failed"
    # 잔고 조회 실패 시 로컬 포지션을 함부로 missing 으로 단정하지 않는다(조회 불가 ≠ 미보유).
    assert report["missing_in_broker"] == []


def test_reconcile_empty_both_ok():
    svc = OverseasReconcileService()
    report = svc.reconcile({}, _balance([]))
    assert report["ok"] is True
