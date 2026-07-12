"""패키지 간 의존성 방향을 고정하는 아키텍처 테스트."""

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_services_do_not_depend_on_task_or_scheduler_packages():
    """서비스 계층은 실행 오케스트레이션 계층을 알지 않아야 한다."""
    forbidden_roots = {"task", "scheduler"}
    violations = []

    for path in (PROJECT_ROOT / "services").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue

            for module in modules:
                if module.split(".", 1)[0] in forbidden_roots:
                    violations.append(f"{path.name}:{node.lineno} -> {module}")

    assert not violations, "서비스 역의존이 발견되었습니다:\n" + "\n".join(violations)
