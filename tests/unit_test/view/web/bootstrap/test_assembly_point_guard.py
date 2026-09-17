"""조립 지점(`service_container.py`) 재비대화를 막는 구조 가드.

#659~664 와 `overseas_bootstrap` 분해로 이 파일을 962→710→936 줄까지 줄였지만,
신규 서비스군 조립이 계속 이 파일에 쌓이는 구조 자체는 그대로라 분해만으로는
재발을 막지 못했다(todo_list.md M-2 감시 항목 — 수기 줄수 계측이 한 달간 갱신되지
않아 증가폭이 문서에 반영되지 않았다).

규약: **신규 서비스군은 `view/web/bootstrap/<이름>_bootstrap.py` 로 분리한다.**
아래 예산을 넘기려면 조립을 옮기거나, 옮길 수 없는 이유를 남기고 예산을
올려야 한다. 예산을 올릴 때는 todo_list.md M-2 계측도 함께 갱신한다.

**2026-09-17 — 가드가 측정하는 곳과 실제로 누적되는 곳이 어긋나 있었다.**
`service_container.py` 예산이 지켜진 이유의 상당 부분은 "해외 조립이 저기로
갔기 때문" 이다 — 08-22 추출 당시 220줄이던 `overseas_bootstrap.py` 가 515줄·
services/task import 30개로 자랐는데 아무 가드도 없었다. 파일 하나만 고정하면
규약이 아니라 그 파일만 지킨 셈이 된다. 그래서 예산을 **bootstrap 디렉터리
전체**로 넓히고, 목록에 없는 새 모듈이 생기면 실패시킨다.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[5]
SERVICE_CONTAINER = PROJECT_ROOT / "view" / "web" / "bootstrap" / "service_container.py"

# 조립 대상을 직접 끌어오는 import 의 뿌리 패키지.
ASSEMBLY_ROOTS = {"services", "task"}

BOOTSTRAP_DIR = SERVICE_CONTAINER.parent

# 2026-08-23 실측: services+task import 53개 / 936줄.
MAX_ASSEMBLY_IMPORTS = 56
MAX_LINES = 980

# 모듈별 예산 (줄수, services/task import 수). 2026-09-17 실측에 여유를 얹은 값이다.
# 새 bootstrap 모듈을 만들면 여기에 등록해야 한다 — 등록하지 않으면 아래
# `test_every_bootstrap_module_has_a_budget` 이 실패한다. 예산을 올릴 때는
# todo_list.md M-2 계측도 함께 갱신한다.
MODULE_BUDGETS = {
    "__init__.py": (40, 2),
    "backtest_task_bootstrap.py": (110, 6),
    "broker_bootstrap.py": (90, 2),
    "config_bootstrap.py": (180, 9),
    "market_data_bootstrap.py": (170, 8),
    "overseas_bootstrap.py": (560, 33),
    "query_bootstrap.py": (205, 9),
    "realtime_bootstrap.py": (200, 10),
    "repository_bootstrap.py": (80, 2),
    "runtime_mode.py": (90, 2),
    "scheduler_bootstrap.py": (250, 2),
    "service_container.py": (MAX_LINES, MAX_ASSEMBLY_IMPORTS),
    "strategy_factory.py": (300, 4),
    "wiring_phase.py": (140, 2),
}

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


def _module_files():
    return sorted(
        path for path in BOOTSTRAP_DIR.glob("*.py") if not path.name.startswith("test_")
    )


def test_every_bootstrap_module_has_a_budget():
    """새 bootstrap 모듈은 예산 목록에 등록돼야 한다.

    이 가드의 사각지대는 "측정하지 않는 파일" 이었다(2026-09-17: overseas_bootstrap
    515줄·import 30개가 무가드 상태였다). 목록에 없는 모듈이 생기면 여기서 막는다.
    """
    present = {path.name for path in _module_files()}
    unbudgeted = sorted(present - MODULE_BUDGETS.keys())
    assert not unbudgeted, (
        f"예산이 없는 bootstrap 모듈입니다: {unbudgeted}. "
        f"MODULE_BUDGETS 에 (줄수, services/task import 수) 를 등록하십시오. "
        f"{_EXTRACT_HINT}"
    )

    stale = sorted(MODULE_BUDGETS.keys() - present)
    assert not stale, (
        f"예산 목록에는 있으나 파일이 없습니다: {stale}. "
        f"모듈을 옮기거나 지웠다면 MODULE_BUDGETS 도 함께 갱신하십시오."
    )


def test_bootstrap_modules_stay_within_budget():
    """조립이 `service_container.py` 밖으로 옮겨가 그대로 쌓이는 것을 막는다."""
    over = []
    for path in _module_files():
        # 미등록 모듈은 `test_every_bootstrap_module_has_a_budget` 가 잡는다 —
        # 여기서 KeyError 로 죽으면 그쪽 실패 메시지가 묻힌다.
        if path.name not in MODULE_BUDGETS:
            continue
        max_lines, max_imports = MODULE_BUDGETS[path.name]
        line_count = len(path.read_text(encoding="utf-8-sig").splitlines())
        import_count = len(_assembly_imports(path))
        if line_count > max_lines:
            over.append(f"{path.name}: {line_count}줄 > 예산 {max_lines}")
        if import_count > max_imports:
            over.append(f"{path.name}: import {import_count}개 > 예산 {max_imports}")

    assert not over, f"bootstrap 예산 초과: {over}. {_EXTRACT_HINT}"


def test_budget_detector_flags_an_over_budget_module(tmp_path):
    """detector 가 실제로 초과를 잡는지 합성 모듈로 확인한다(vacuous guard 방지)."""
    synthetic = tmp_path / "synthetic_bootstrap.py"
    synthetic.write_text(
        "from services.a_service import A\n"
        "from services.b_service import B\n"
        "from task.background.c_task import C\n",
        encoding="utf-8",
    )

    assert len(_assembly_imports(synthetic)) == 3, "detector 가 조립 import 를 세지 못했습니다."
    assert len(synthetic.read_text(encoding="utf-8-sig").splitlines()) == 3
