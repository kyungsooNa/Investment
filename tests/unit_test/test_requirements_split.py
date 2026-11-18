"""운영 의존성(requirements.txt)과 개발·테스트 의존성(requirements-dev.txt) 분리 가드."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV_ONLY = {"pytest", "pytest-cov", "pytest-xdist", "pytest-mock", "pytest-asyncio", "pytest-timeout", "debugpy"}


def _package_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(re.split(r"[\[<>=!~ ;]", line, maxsplit=1)[0].lower())
    return names


def test_runtime_requirements_exclude_test_and_debug_packages():
    leaked = _package_names(ROOT / "requirements.txt") & DEV_ONLY
    assert not leaked, f"운영 의존성에 개발 전용 패키지가 섞였습니다: {sorted(leaked)} → requirements-dev.txt 로 옮기십시오."


def test_dev_requirements_include_runtime_and_test_packages():
    dev = ROOT / "requirements-dev.txt"
    lines = [line.strip() for line in dev.read_text(encoding="utf-8").splitlines()]
    assert "-r requirements.txt" in lines
    assert DEV_ONLY <= _package_names(dev)
