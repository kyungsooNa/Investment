"""strategies/*.py 의 entry_rejected 로그 레벨 회귀 방지 (P2 2-2 후속).

운영 `LOG_LEVEL=INFO` 환경에서 `scan_metrics.rejected_reasons` 가 정상 수집되려면
`_logger.debug({"event": "entry_rejected", ...})` 패턴이 없어야 한다. 본 테스트는
ast 파싱으로 모든 strategy 모듈을 검사해 신규 PR 에서 debug-level entry_rejected 가
재유입되지 않도록 한다.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path


_STRATEGY_DIR = Path(__file__).resolve().parents[3] / "strategies"


def _iter_strategy_modules():
    for name in sorted(os.listdir(_STRATEGY_DIR)):
        if not name.endswith(".py"):
            continue
        if name == "__init__.py":
            continue
        yield _STRATEGY_DIR / name


def _is_debug_logger_call(node: ast.Call) -> bool:
    """`self._logger.debug(...)` 또는 `_logger.debug(...)` 형태인지 검사."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "debug":
        return False
    inner = func.value
    # _logger.debug / self._logger.debug
    if isinstance(inner, ast.Name) and inner.id == "_logger":
        return True
    if isinstance(inner, ast.Attribute) and inner.attr == "_logger":
        return True
    return False


def _extract_event_value(arg: ast.expr) -> str | None:
    """call arg 가 dict literal 이고 'event' 키 string 값을 가지면 그 값을 반환."""
    if not isinstance(arg, ast.Dict):
        return None
    for key, value in zip(arg.keys, arg.values):
        if isinstance(key, ast.Constant) and key.value == "event":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


def test_no_entry_rejected_at_debug_level_in_strategies():
    """strategies/*.py 어느 곳에도 `_logger.debug({"event": "entry_rejected", ...})` 가 없어야 한다."""
    offenders = []

    for path in _iter_strategy_modules():
        with open(path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_debug_logger_call(node):
                continue
            if not node.args:
                continue
            event_value = _extract_event_value(node.args[0])
            if event_value == "entry_rejected":
                offenders.append(f"{path.name}:L{node.lineno}")

    assert offenders == [], (
        "scan_metrics.rejected_reasons 가 prod LOG_LEVEL=INFO 환경에서 누락되지 않도록, "
        "다음 위치의 entry_rejected 로그는 _logger.info 로 변경해야 한다: " + ", ".join(offenders)
    )
