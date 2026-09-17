"""알림 계약(`docs/notification_alert_contracts.md`) 구조 가드.

M-5 의 잔여 항목이다. 알림별 **동작** 테스트는 이미 있다
(`test_favorite_price_alert_service.py` · `test_market_index_threshold_alert_task.py` 등).
문제는 그 테스트들이 **이미 계약표에 있는 알림만** 덮는다는 점이다 — 새 알림 계열이
계약표 밖에서 늘어나도 전부 통과한다. 실제로 08-18 문서화 이후 한 달 만에 알림 5계열이
계약표 없이 추가됐고, 그 공백에서 #950(같은 첨부 문구 중복)·#957(release dedup 키
정규화)이 **이 문서가 이미 정리해 둔 실패 모드 그대로** 재발했다.

그래서 이 파일은 동작이 아니라 **구조**를 고정한다. `test_quote_subscription_contract_guard.py`
(M-9)·`test_assembly_point_guard.py`(#888) 와 같은 형식이다: 알려진 목록을 박아두고,
목록이 바뀌면 실패시키면서 계약 문서를 읽게 한다. **실패했다고 코드가 틀린 것은 아니다**
— 새 발신 경로를 알림/리포트 중 무엇으로 볼지 분류하고, 알림이면 계약표에 행을 추가하라는
뜻이다.

검출축은 `services/telegram_notifier.py` 의 `send_*` 메서드다. 외부(텔레그램)로 나가는
발신 표면이라 중복 억제가 가장 중요한 지점이기 때문이다.
**범위 한계(의도된 것)**: `notification_service` 만 거치는 내부 알림은 이 detector 가
잡지 않는다(YouTube 다이제스트가 그 경우다 — 계약표에는 행이 있으나 자동 검출 대상은
아니다). 내부 알림까지 넓히면 거의 모든 태스크가 걸려 신호가 죽는다.

가드가 비어 있지 않다는 것(vacuous guard 방지)은 detector 를 합성 소스로 검증하는
`test_*_detector_*` 케이스로 함께 고정한다.
"""
import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DOC = PROJECT_ROOT / "docs" / "notification_alert_contracts.md"
NOTIFIER = PROJECT_ROOT / "services" / "telegram_notifier.py"

_DOC_HINT = f"계약과 목록을 함께 확인할 것: {CONTRACT_DOC.relative_to(PROJECT_ROOT)}"

# 이벤트성 알림 — 같은 사건이 두 번 나가면 안 되므로 계약표에 행이 있어야 한다.
# 계약표에는 아래 메서드 이름이 그대로 등장해야 한다(백틱 표기).
CONTRACTED_ALERT_SENDERS = {
    "send_national_trade_trend_report",
    "send_jeju_semiconductor_trade_report",
    "send_jeju_trade_pending_report",
    "send_intraday_volume_surge_alert",
    "send_disclosure_alert",
    "send_disclosure_digest",
}

# 정기 리포트 — 스케줄 1회 발송이라 사건 단위 중복 억제 계약이 없다.
# 여기에 넣는다는 것은 "이 발신은 dedup 키가 필요 없다" 는 판단을 남기는 것이다.
PERIODIC_REPORT_SENDERS = {
    "send_ranking_report",
    "send_period_investor_ranking_report",
    "send_ytd_ranking_report",
    "send_daily_theme_report",
    "send_newhigh_report",
    "send_strategy_log_report",
    "send_operational_decision_report",
    "send_premium_watchlist_report",
    "send_minervini_report",
    "send_market_cap_gap_report",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _send_methods(source: str) -> set:
    """`TelegramNotifier` 의 발신 메서드 이름을 AST 로 수집한다.

    문자열 검색은 docstring·로그 문구에 적힌 이름을 함께 잡아 목록을 부풀린다.
    """
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("send_"):
            found.add(node.name)
    return found


def _contract_table_rows(doc: str) -> list:
    """계약표(`| 알림 | ... |`)의 데이터 행을 셀 리스트로 돌려준다."""
    rows = []
    for line in doc.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0] in ("알림", "") or set(cells[0]) <= {"-", ":", " "}:
            continue
        rows.append(cells)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 발신 경로가 분류되지 않은 채 늘지 않게
# ─────────────────────────────────────────────────────────────────────────────


def test_every_telegram_sender_is_classified():
    """새 발신 메서드는 알림/리포트 중 하나로 분류돼야 한다."""
    methods = _send_methods(_read(NOTIFIER))
    assert methods, "TelegramNotifier 에서 발신 메서드를 찾지 못했습니다(detector 고장)."

    classified = CONTRACTED_ALERT_SENDERS | PERIODIC_REPORT_SENDERS
    unclassified = sorted(methods - classified)
    assert not unclassified, (
        f"분류되지 않은 발신 메서드가 있습니다: {unclassified}. "
        f"같은 사건이 두 번 나가면 안 되는 알림이면 CONTRACTED_ALERT_SENDERS 에 넣고 "
        f"계약표에 행을 추가하십시오. 스케줄 1회 발송 리포트면 PERIODIC_REPORT_SENDERS 에 "
        f"넣으십시오. {_DOC_HINT}"
    )

    stale = sorted(classified - methods)
    assert not stale, (
        f"목록에는 있으나 코드에 없는 발신 메서드입니다: {stale}. "
        f"이름을 바꿨거나 지웠다면 이 목록과 계약표를 함께 갱신하십시오. {_DOC_HINT}"
    )


def test_contracted_alert_senders_appear_in_the_contract_table():
    """계약 대상 알림은 계약표에서 코드 진입점으로 식별 가능해야 한다."""
    doc = _read(CONTRACT_DOC)
    missing = sorted(name for name in CONTRACTED_ALERT_SENDERS if f"`{name}`" not in doc)
    assert not missing, (
        f"계약표에서 찾을 수 없는 알림 진입점입니다: {missing}. "
        f"계약표 행에 발신 조건·중복 억제·재시작 복원·테스트를 적고 진입점을 "
        f"백틱으로 표기하십시오. {_DOC_HINT}"
    )


def test_sender_detector_flags_a_new_method():
    """detector 가 실제로 새 발신 메서드를 잡는지 합성 소스로 확인한다."""
    synthetic = (
        "class TelegramNotifier:\n"
        "    async def send_brand_new_alert(self, payload):\n"
        "        '''send_not_a_method 는 docstring 이므로 잡히면 안 된다.'''\n"
        "        return True\n"
    )
    found = _send_methods(synthetic)
    assert found == {"send_brand_new_alert"}, (
        f"detector 가 새 발신 메서드를 정확히 잡지 못했습니다: {found}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 계약표 행이 비어 있지 않게
# ─────────────────────────────────────────────────────────────────────────────


def test_contract_rows_fill_every_column():
    """계약표는 발신 조건·중복 억제·재시작 복원·테스트를 모두 채워야 한다."""
    rows = _contract_table_rows(_read(CONTRACT_DOC))
    assert len(rows) >= len(CONTRACTED_ALERT_SENDERS), (
        f"계약표 행이 {len(rows)}개뿐입니다. 등재된 알림 진입점 "
        f"{len(CONTRACTED_ALERT_SENDERS)}개를 담지 못합니다. {_DOC_HINT}"
    )

    empty = [
        (row[0], idx)
        for row in rows
        for idx, cell in enumerate(row[:5])
        if not cell or set(cell) <= {"-", " "}
    ]
    assert not empty, (
        f"계약표에 빈 칸이 있습니다: {empty}. "
        f"'없음' 이면 그 이유까지 적으십시오 — 빈 칸은 '아직 안 정했다' 와 "
        f"'의도적으로 없다' 를 구분하지 못합니다. {_DOC_HINT}"
    )


def test_empty_cell_detector_flags_a_blank_row():
    """빈 칸 detector 가 실제로 빈 셀을 잡는지 합성 표로 확인한다."""
    synthetic = (
        "| 알림 | 발신 조건 | 중복 억제 | 재시작 복원 | 테스트 기준 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 합성 알림 | 조건 |  | 없음 | `tests/x.py` |\n"
    )
    rows = _contract_table_rows(synthetic)
    assert len(rows) == 1, f"합성 표에서 데이터 행을 잡지 못했습니다: {rows}"
    blanks = [idx for idx, cell in enumerate(rows[0][:5]) if not cell]
    assert blanks == [2], f"빈 칸을 정확히 잡지 못했습니다: {blanks}"


# ─────────────────────────────────────────────────────────────────────────────
# 문서 자체가 낡지 않게
# ─────────────────────────────────────────────────────────────────────────────


def test_contract_doc_references_existing_tests():
    """계약표가 가리키는 테스트 파일이 실제로 있는지 확인한다."""
    doc = _read(CONTRACT_DOC)
    referenced = set(re.findall(r"`(test_[\w]+\.py)`", doc))
    assert referenced, "계약 문서에서 테스트 파일 이름을 찾지 못했습니다."

    known = {path.name for path in (PROJECT_ROOT / "tests").rglob("test_*.py")}
    missing = sorted(name for name in referenced if name not in known)
    assert not missing, (
        f"계약 문서가 없는 테스트 파일을 가리킵니다: {missing}. "
        f"테스트를 옮기거나 이름을 바꿨다면 계약표도 함께 갱신하십시오. {_DOC_HINT}"
    )
