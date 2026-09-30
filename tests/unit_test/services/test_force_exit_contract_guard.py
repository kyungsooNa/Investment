"""강제청산 계약(`docs/force_exit_contracts.md`) 구조 가드.

강제청산 경계 조건 수정이 #899~#901 뒤 #1000에서 다시 발생해, 개별 회귀 테스트를
넘어 경로 전체의 불변조건을 고정한다. 이 테스트가 실패하면 코드를 무조건 되돌리는
것이 아니라 계약 문서와 허용 목록을 함께 검토해야 한다.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DOC = PROJECT_ROOT / "docs" / "force_exit_contracts.md"

FORCE_EXIT_SOURCE_MODULES = {
    "scheduler/strategy_scheduler.py",
    "services/broker_order_submitter.py",
    "services/fill_reconciliation_service.py",
    "services/order_execution_service.py",
    "services/order_submission_coordinator.py",
    "services/risk_gate_service.py",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _production_force_exit_source_modules() -> set[str]:
    found = set()
    for root in ("scheduler", "services"):
        for path in (PROJECT_ROOT / root).rglob("*.py"):
            if "strategy_force_exit:" in _read(path):
                found.add(path.relative_to(PROJECT_ROOT).as_posix())
    return found


def test_force_exit_contract_doc_exists_and_names_the_regression_tests():
    doc = _read(CONTRACT_DOC)

    for test_name in (
        "test_strategy_scheduler_time_window.py",
        "test_strategy_scheduler_force_exit_retry.py",
        "test_broker_order_submitter.py",
        "test_order_execution_service.py",
    ):
        assert f"`{test_name}`" in doc


def test_force_exit_tiers_end_flat_before_the_order_cutoff():
    from scheduler.strategy_scheduler import StrategyScheduler

    tiers = StrategyScheduler.FORCE_EXIT_TIERS
    assert tiers
    assert tiers[-1][1] == 1.0, "마지막 강제청산 tier는 누적 전량(1.0)이어야 합니다."
    assert tiers[-1][0] > StrategyScheduler.ORDER_CUTOFF_MINUTES_BEFORE_CLOSE, (
        "최종 강제청산 tier가 주문 컷오프보다 늦거나 같으면 잔량이 오버나이트됩니다."
    )
    assert tiers == sorted(tiers, key=lambda item: item[0], reverse=True)


def test_force_exit_source_prefix_consumers_stay_on_the_allowlist():
    actual = _production_force_exit_source_modules()
    assert actual == FORCE_EXIT_SOURCE_MODULES, (
        "강제청산 source 계약 소비 경로가 바뀌었습니다. 새 경로가 source를 일반 전략이나 "
        "수동 주문으로 오분류하지 않는지 확인하고 docs/force_exit_contracts.md와 이 목록을 "
        f"함께 갱신하십시오. 기대={sorted(FORCE_EXIT_SOURCE_MODULES)}, 실제={sorted(actual)}"
    )


def test_force_exit_source_is_forwarded_to_the_broker_submitter():
    source = _read(PROJECT_ROOT / "services" / "order_submission_coordinator.py")
    call_start = source.index("self._broker_submitter.submit_with_retry(")
    call_end = source.index("\n            )", call_start)
    call = source[call_start:call_end]

    assert "source=source" in call, (
        "강제청산 source가 broker submitter까지 전달되지 않으면 timeout 후 잔고 없음 대사를 "
        "일반 매도와 구분할 수 없습니다."
    )


def test_force_exit_source_detector_flags_a_new_consumer(tmp_path):
    path = tmp_path / "new_consumer.py"
    path.write_text("SOURCE = 'strategy_force_exit:'\n", encoding="utf-8")
    assert "strategy_force_exit:" in _read(path)
