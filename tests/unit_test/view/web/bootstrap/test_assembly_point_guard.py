"""조립 지점(`service_container.py`) 재비대화를 막는 구조 가드.

#659~664 와 `overseas_bootstrap` 분해로 이 파일을 962→710→936 줄까지 줄였지만,
신규 서비스군 조립이 계속 이 파일에 쌓이는 구조 자체는 그대로라 분해만으로는
재발을 막지 못했다(todo_list.md M-2 감시 항목 — 수기 줄수 계측이 한 달간 갱신되지
않아 증가폭이 문서에 반영되지 않았다).

규약: **신규 서비스군은 `view/web/bootstrap/<이름>_bootstrap.py` 로 분리한다.**
아래 예산을 넘기려면 조립을 옮기거나, 옮길 수 없는 이유를 남기고 예산을
올려야 한다. 예산을 올릴 때는 todo_list.md M-2 계측도 함께 갱신한다.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[5]
SERVICE_CONTAINER = PROJECT_ROOT / "view" / "web" / "bootstrap" / "service_container.py"

# 조립 대상을 직접 끌어오는 import 의 뿌리 패키지.
ASSEMBLY_ROOTS = {"services", "task"}

# 2026-08-23 실측: services+task import 53개 / 936줄.
MAX_ASSEMBLY_IMPORTS = 56
MAX_LINES = 980

_EXTRACT_HINT = (
    "신규 서비스군은 `view/web/bootstrap/<이름>_bootstrap.py` 로 분리한다 "
    "(#659~664 · overseas_bootstrap 패턴). "
    "예산을 올려야 한다면 todo_list.md M-2 계측도 함께 갱신할 것."
)


def _assembly_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".", 1)[0] in ASSEMBLY_ROOTS
    ]


def test_service_container_does_not_accumulate_assembly_imports():
    """조립 지점이 직접 끌어오는 서비스/태스크 수를 예산 안에 묶는다."""
    imports = _assembly_imports(SERVICE_CONTAINER)

    assert len(imports) <= MAX_ASSEMBLY_IMPORTS, (
        f"service_container.py 의 services/task import 가 {len(imports)}개로 "
        f"예산({MAX_ASSEMBLY_IMPORTS})을 넘었습니다. {_EXTRACT_HINT}"
    )


def test_service_container_stays_within_line_budget():
    """줄수 계측을 문서 수기 갱신이 아니라 테스트로 고정한다."""
    line_count = len(SERVICE_CONTAINER.read_text(encoding="utf-8-sig").splitlines())

    assert line_count <= MAX_LINES, (
        f"service_container.py 가 {line_count}줄로 예산({MAX_LINES})을 넘었습니다. "
        f"{_EXTRACT_HINT}"
    )
