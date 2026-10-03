"""KRX 애프터마켓(16:00~20:00) 장 운영 확장이 수동 주문 경로 밖으로 새지 않는지 고정한다 (todo 0-3).

`MarketCalendarService.is_market_open_now(include_krx_after_market=True)` 는 opt-in 이다.
전략 스케줄러·강제청산·장마감 태스크가 이 인자를 켜면 16:00 이후에도 장이 열린 것으로 보인다.
새 호출 지점을 추가하려면 수동 주문 전용인지 확인한 뒤 아래 허용 목록을 갱신한다.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_DIRS = ("brokers", "core", "scheduler", "services", "strategies", "task", "view", "scripts")

# 시간외 단일가 주문 구분일 때만 켜는 수동 주문 경로
ALLOWED_CALLERS = {"services/order_execution_service.py"}


def _callers_enabling_after_market():
    callers = set()
    for scan_dir in SCAN_DIRS:
        for path in (REPO_ROOT / scan_dir).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg != "include_krx_after_market":
                        continue
                    if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                        continue
                    callers.add(path.relative_to(REPO_ROOT).as_posix())
    return callers


def test_only_manual_order_path_enables_krx_after_market():
    assert _callers_enabling_after_market() == ALLOWED_CALLERS
