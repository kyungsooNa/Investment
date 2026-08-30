"""시세/구독 계약(`docs/quote_subscription_contracts.md`) 구조 가드.

M-9 의 잔여 항목("계약을 테스트로 고정")이다. 계약별 **동작** 테스트는 이미 있다
(`test_price_stream_service.py` · `test_favorite_service.py` · `test_subscription_policy.py`).
문제는 그 테스트들이 **현재 경로만** 덮는다는 점이다 — 새 서비스가 공유 캐시에 쓰기
시작하거나, 폴백 단계를 하나 끼워 넣거나, 정책 밖에서 구독을 걸어도 전부 통과한다.
계약이 반복해서 깨진 경로가 정확히 그 "새로 추가되는 경로" 였다(#858/#871 · #897/#902 ·
#906~#910).

그래서 이 파일은 동작이 아니라 **구조**를 고정한다. `test_assembly_point_guard.py`(#888)·
`test_static_asset_versions.py`(#915) 와 같은 형식이다: 알려진 목록을 박아두고, 목록이
바뀌면 실패시키면서 계약 문서를 읽게 한다. **실패했다고 코드가 틀린 것은 아니다** —
계약을 확인하고 목록을 갱신하라는 뜻이다.

가드가 비어 있지 않다는 것(vacuous guard 방지)은 각 detector 를 합성 위반 소스로
검증하는 `test_*_detector_*` 케이스로 함께 고정한다. detector 가 아무것도 못 잡으면
가드 전체가 조용히 무력해지기 때문이다.
"""
import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DOC = PROJECT_ROOT / "docs" / "quote_subscription_contracts.md"

_DOC_HINT = f"계약과 목록을 함께 확인할 것: {CONTRACT_DOC.relative_to(PROJECT_ROOT)}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _production_python_files():
    """프로덕션 파이썬 파일(테스트·스크립트 제외)."""
    skip_roots = {"tests", "scripts", "logs", "data", "reports", "docs"}
    for path in PROJECT_ROOT.rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT)
        if rel.parts[0] in skip_roots or rel.parts[0].startswith("."):
            continue
        yield rel, path


# ─────────────────────────────────────────────────────────────────────────────
# detector — 소스 텍스트만 받는 순수 함수 (합성 위반으로 자체 검증한다)
# ─────────────────────────────────────────────────────────────────────────────


def _called_attributes(source: str, wanted: set) -> set:
    """`obj.method(...)` 형태로 호출된 메서드명 중 `wanted` 에 속한 것."""
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in wanted
        ):
            found.add(node.func.attr)
    return found


def _assigned_tuple_orders(source: str, variable: str) -> list:
    """`variable = (self.a, self.b, ...)` 대입들을 등장 순서대로 속성명 리스트로."""
    tree = ast.parse(source)
    orders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Tuple):
            continue
        if not any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            continue
        orders.append(
            [el.attr for el in node.value.elts if isinstance(el, ast.Attribute)]
        )
    return orders


def _first_call_line(source: str, func_name: str, callee: str):
    """`func_name` 안에서 `callee` 가 처음 호출되는 줄 번호. 없으면 None.

    문자열 검색은 docstring 에 적힌 이름까지 잡아 순서 판정이 뒤집힌다 —
    실제 호출 노드만 본다.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
            continue
        lines = [
            inner.lineno
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == callee
        ]
        return min(lines) if lines else None
    return None


def _method_bodies(source: str, prefix: str) -> dict:
    """이름이 `prefix` 로 시작하는 (async) 메서드의 소스 조각."""
    tree = ast.parse(source)
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(prefix):
            bodies[node.name] = ast.unparse(node)
    return bodies


# ─────────────────────────────────────────────────────────────────────────────
# 계약 1. 캐시 키 — 종목코드 단위 공유 캐시는 통합(UNIFIED) 시세 전용
# ─────────────────────────────────────────────────────────────────────────────

# `StockRepository` 파사드의 현재가 쓰기 API. 종목코드만으로 키를 잡으므로
# 거래소 지정 값이 들어오면 통합 값과 충돌한다(#858/#871).
SHARED_PRICE_WRITE_METHODS = {
    "set_current_price",
    "update_current_price",
    "update_realtime_data",
}

# 2026-08-30 실측. 새 모듈이 여기 들어오려면 그 경로가 **통합 기준 값만** 다루는지
# 확인해야 한다. 거래소·시장·통화처럼 같은 종목코드에 값이 여러 개 존재할 수 있는
# 축을 쓴다면 공유 캐시가 아니라 그 축을 키에 포함한 저장소를 써야 한다.
SHARED_PRICE_WRITERS = {
    "services/market_data_service.py",
    "services/price_stream_service.py",
}


def test_shared_price_cache_writers_stay_on_the_allowlist():
    """공유 현재가 캐시에 쓰는 프로덕션 모듈 목록을 고정한다 (계약 1)."""
    writers = set()
    for rel, path in _production_python_files():
        if rel.parts[0] == "repositories":
            continue  # 저장소 자신의 정의부
        if _called_attributes(_read(path), SHARED_PRICE_WRITE_METHODS):
            writers.add(rel.as_posix())

    assert writers == SHARED_PRICE_WRITERS, (
        f"공유 현재가 캐시({sorted(SHARED_PRICE_WRITE_METHODS)}) 쓰기 경로가 바뀌었습니다.\n"
        f"  기대: {sorted(SHARED_PRICE_WRITERS)}\n"
        f"  실제: {sorted(writers)}\n"
        f"공유 캐시는 종목코드 단위라 통합(H0UNCNT0) 시세 전용입니다. 거래소·시장·통화처럼 "
        f"같은 코드에 값이 여러 개 존재할 수 있는 축을 다루면 공유 캐시에 넣지 말고 "
        f"그 축을 키에 포함한 저장소를 쓰고, 우회 경로에는 계측을 남기십시오. "
        f"{_DOC_HINT}"
    )


def test_exchange_specific_ticks_return_before_touching_the_shared_cache():
    """거래소 지정 틱의 조기 반환이 공유 저장소 쓰기보다 앞에 있다 (계약 1)."""
    source = _read(PROJECT_ROOT / "services" / "price_stream_service.py")
    guard_line = _first_call_line(source, "on_price_tick", "_tick_exchange")
    write_line = _first_call_line(source, "on_price_tick", "update_realtime_data")

    assert guard_line is not None, (
        "on_price_tick 에서 거래소 판정(`_tick_exchange`)이 사라졌습니다. "
        f"거래소 지정 틱이 공유 캐시를 덮어쓰면 #871 이 재발합니다. {_DOC_HINT}"
    )
    assert write_line is not None and guard_line < write_line, (
        "on_price_tick 이 거래소를 판정하기 전에 공유 저장소에 씁니다. "
        f"거래소 지정 틱은 SSE 큐로만 fanout 하고 반환해야 합니다. {_DOC_HINT}"
    )


def test_shared_cache_writer_detector_flags_a_new_writer():
    """detector 자체 검증 — 새 쓰기 경로를 실제로 잡는가."""
    violation = "class New:\n    def go(self):\n        self._stock_repo.set_current_price('005930', {})\n"
    assert _called_attributes(violation, SHARED_PRICE_WRITE_METHODS) == {"set_current_price"}
    assert _called_attributes("x = 1\n", SHARED_PRICE_WRITE_METHODS) == set()


# ─────────────────────────────────────────────────────────────────────────────
# 계약 2. 폴백 체인 — 단계 순서는 장 운영 여부에 종속, 완료 판정은 값 기준
# ─────────────────────────────────────────────────────────────────────────────

# 장중/장외 순서가 다른 이유: KIS 현재가 API 가 장전·비거래일에 등락률을 `0.00` 으로
# 돌려주므로, 장외에는 DB 일봉을 먼저 둬야 같은 표의 기준일이 섞이지 않는다(#902).
MARKET_OPEN_STAGES = [
    "_fill_from_query_service",
    "_fill_from_memory_cache",
    "_fill_from_daily_snapshot",
]
MARKET_CLOSED_STAGES = [
    "_fill_from_daily_snapshot",
    "_fill_from_query_service",
    "_fill_from_memory_cache",
]


def test_favorite_fallback_stage_order_is_pinned_per_market_state():
    """장중/장외 폴백 단계 순서를 고정한다 (계약 2-1)."""
    source = _read(PROJECT_ROOT / "services" / "favorite_service.py")
    orders = _assigned_tuple_orders(source, "stages")

    assert orders == [MARKET_OPEN_STAGES, MARKET_CLOSED_STAGES], (
        f"즐겨찾기 시세 폴백 단계가 바뀌었습니다.\n"
        f"  기대(장중, 장외): {[MARKET_OPEN_STAGES, MARKET_CLOSED_STAGES]}\n"
        f"  실제: {orders}\n"
        f"단계를 끼우거나 순서를 바꿀 때는 **기준일 일관성**을 먼저 확인하십시오 — "
        f"장외에 라이브 값을 먼저 두면 같은 표에 `0.00%`(API)와 실제 등락률(DB)이 섞입니다. "
        f"{_DOC_HINT}"
    )


def test_every_fallback_stage_judges_completeness_by_value():
    """모든 폴백 단계가 `_apply_price_rate` 로 완료를 판정한다 (계약 2-2)."""
    source = _read(PROJECT_ROOT / "services" / "favorite_service.py")
    stages = _method_bodies(source, "_fill_from_")

    assert stages, "폴백 단계(`_fill_from_*`)를 하나도 찾지 못했습니다."

    missing = [name for name, body in stages.items() if "_apply_price_rate" not in body]
    assert not missing, (
        f"폴백 단계 {missing} 가 `_apply_price_rate` 를 거치지 않습니다. "
        f"`rt_cd == '0'` 은 충분조건이 아닙니다 — 응답이 성공이어도 값이 비면 다음 단계로 "
        f"가야 하고(#897), 부분 값만 주는 소스는 값을 남기되 미완료로 보고해야 뒤 단계가 "
        f"덮어쓸 수 있습니다. {_DOC_HINT}"
    )


@pytest.mark.parametrize(
    "data, expected_complete, expected_price",
    [
        ({"output": {}}, False, None),                                        # 값 없음 → 다음 단계로
        ({"output": {"stck_prpr": "70000"}}, False, "70000"),                 # 부분 값 → 남기되 미완료
        ({"output": {"stck_prpr": "70000", "prdy_ctrt": "1.2"}}, True, "70000"),  # 완전 → 멈춤
    ],
)
def test_apply_price_rate_keeps_three_states(data, expected_complete, expected_price):
    """`_apply_price_rate` 3상태(없음/부분/완전)를 고정한다 (계약 2-2)."""
    from services.favorite_service import _apply_price_rate

    entry = {}
    assert _apply_price_rate(entry, data) is expected_complete
    assert entry.get("price") == expected_price


def test_stage_order_detector_flags_a_reordering():
    """detector 자체 검증 — 순서 변경을 실제로 잡는가."""
    source = "def f(self):\n    stages = (self._b, self._a)\n"
    assert _assigned_tuple_orders(source, "stages") == [["_b", "_a"]]
    assert _assigned_tuple_orders("x = 1\n", "stages") == []


# ─────────────────────────────────────────────────────────────────────────────
# 계약 3. 슬롯 회계 — 진실 소스는 브로커 원장, CRITICAL 은 거절
# ─────────────────────────────────────────────────────────────────────────────

# 문서 규칙: "새 우선순위를 만들지 말고 기존 4단계에 맞춘다."
EXPECTED_PRIORITIES = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# 정책을 거치지 않고 브로커/스트리밍에 직접 구독을 거는 프로덕션 모듈.
# 이런 경로는 `SubscriptionPolicy` 슬롯 계산에 안 잡히므로
# `set_external_reserved_slots` 로 예약분에 반영돼야 한다(계약 3-3).
POLICY_BYPASSING_SUBSCRIBERS = {
    "services/streaming_service.py",        # 정책이 사용하는 하위 계층
    "services/order_execution_service.py",  # 주문 체결 감시용 호가 구독
}

EXTERNAL_RESERVED_SLOT_CALLERS = {
    "task/background/intraday/websocket_watchdog_task.py",
}


def test_subscription_slot_budget_and_priorities_are_pinned():
    """슬롯 한도와 우선순위 4단계를 고정한다 (계약 3-2)."""
    from services.subscription_policy import SubscriptionPolicy, SubscriptionPriority

    assert SubscriptionPolicy.MAX_WS_SLOTS == 40, (
        f"KIS 웹소켓 슬롯 한도가 바뀌었습니다. {_DOC_HINT}"
    )

    actual = {p.name: int(p) for p in SubscriptionPriority}
    assert actual == EXPECTED_PRIORITIES, (
        f"구독 우선순위가 바뀌었습니다.\n  기대: {EXPECTED_PRIORITIES}\n  실제: {actual}\n"
        f"새 우선순위를 만들지 말고 기존 4단계에 맞추십시오. 밀어내기 불가 요구가 있으면 "
        f"CRITICAL 처럼 **거절**로 설계합니다 — 조용한 강등은 진단이 불가능합니다. {_DOC_HINT}"
    )


def test_policy_bypassing_subscribers_stay_on_the_allowlist():
    """정책 밖에서 직접 구독하는 모듈 목록을 고정한다 (계약 3-3)."""
    subscribe_calls = {"subscribe_realtime_price", "subscribe_realtime_quote"}
    infra_roots = {"brokers", "core"}

    callers = set()
    for rel, path in _production_python_files():
        if rel.parts[0] in infra_roots:
            continue  # 브로커/재시도 큐는 정책 아래 배관
        if rel.as_posix() == "services/subscription_policy.py":
            continue  # 정책 자신
        if _called_attributes(_read(path), subscribe_calls):
            callers.add(rel.as_posix())

    assert callers == POLICY_BYPASSING_SUBSCRIBERS, (
        f"정책을 거치지 않는 구독 경로가 바뀌었습니다.\n"
        f"  기대: {sorted(POLICY_BYPASSING_SUBSCRIBERS)}\n"
        f"  실제: {sorted(callers)}\n"
        f"새 구독 경로는 `SubscriptionPolicy` 슬롯 계산에 포함되는지 먼저 정하고, "
        f"포함되지 않으면 `set_external_reserved_slots` 로 예약분에 넣으십시오. "
        f"둘 다 안 하면 한도(40)를 조용히 넘깁니다. 해지·재연결·재시작 경로에서 슬롯이 "
        f"회수되는지도 함께 확인하십시오. {_DOC_HINT}"
    )


def test_external_reserved_slots_have_a_caller():
    """예약분 반영 경로가 살아 있는지 고정한다 (계약 3-3)."""
    callers = set()
    for rel, path in _production_python_files():
        if rel.parts[0] == "services":
            continue  # 정책 자신의 정의부
        if "set_external_reserved_slots" in _read(path):
            callers.add(rel.as_posix())

    assert callers == EXTERNAL_RESERVED_SLOT_CALLERS, (
        f"`set_external_reserved_slots` 호출처가 바뀌었습니다.\n"
        f"  기대: {sorted(EXTERNAL_RESERVED_SLOT_CALLERS)}\n"
        f"  실제: {sorted(callers)}\n"
        f"정책 밖 구독(장운영정보·체결통보·거래소 지정 탭)이 예약분에 반영되지 않으면 "
        f"슬롯 회계가 실제 등록과 어긋납니다. {_DOC_HINT}"
    )


def test_used_slot_calculation_prefers_the_broker_ledger():
    """사용 슬롯의 진실 소스가 브로커 원장임을 고정한다 (계약 3-1)."""
    source = _read(PROJECT_ROOT / "services" / "subscription_policy.py")
    body = _method_bodies(source, "_calculate_used_slots")["_calculate_used_slots"]

    ledger_at = body.find("_get_broker_ledger")
    fallback_at = body.find("_external_reserved_slots")

    assert ledger_at != -1, (
        "`_calculate_used_slots` 가 브로커 원장을 보지 않습니다. 내부 장부는 재연결·직접 "
        f"구독 경로에서 실제 등록과 어긋납니다(#907). {_DOC_HINT}"
    )
    assert fallback_at == -1 or ledger_at < fallback_at, (
        "내부 장부가 브로커 원장보다 먼저 쓰입니다. 원장이 있으면 그 `total` 을 그대로 "
        f"써야 합니다. {_DOC_HINT}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 문서 자체가 낡지 않게
# ─────────────────────────────────────────────────────────────────────────────


def test_contract_doc_references_existing_tests():
    """계약 문서의 '관련 테스트' 표가 가리키는 파일이 실제로 있는지 확인한다."""
    import re

    referenced = set(re.findall(r"`(tests/unit_test/[\w/]+\.py)`", _read(CONTRACT_DOC)))
    assert referenced, "계약 문서에서 관련 테스트 경로를 찾지 못했습니다."

    missing = sorted(p for p in referenced if not (PROJECT_ROOT / p).exists())
    assert not missing, (
        f"계약 문서가 없는 테스트 파일을 가리킵니다: {missing}. "
        f"테스트를 옮기거나 이름을 바꿨다면 {CONTRACT_DOC.name} 의 '관련 테스트' 표도 "
        f"함께 갱신하십시오."
    )
