"""미국장 USD 원장 체결 대사 테스트.

핵심 계약 3가지:
1. **진실 소스는 브로커 체결내역** — 원장은 주문 접수 시점 기록이라 과다 계상될 수 있다.
2. **불확실하면 건드리지 않는다** — 주문번호가 없거나 조회 구간에 없는 lot 을 미체결로
   단정하면 실제 보유가 원장에서 사라진다.
3. **기본은 판정만** — `apply=True` 를 명시해야 원장이 바뀐다.
"""
import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from repositories.overseas_trade_repository import OverseasTradeRepository
from services.overseas_fill_reconcile_service import OverseasFillReconcileService


class _StubQueryService:
    """`get_overseas_order_history` 만 흉내내는 스텁."""

    def __init__(self, rows, rt_cd=ErrorCode.SUCCESS.value):
        self._rows = rows
        self._rt_cd = rt_cd
        self.calls = []

    async def get_overseas_order_history(self, **kwargs):
        self.calls.append(kwargs)
        return ResCommonResponse(rt_cd=self._rt_cd, msg1="", data={"output": self._rows})


@pytest.fixture
def repo(tmp_path):
    return OverseasTradeRepository(db_path=str(tmp_path / "overseas_trade.db"))


def _service(repo, rows, rt_cd=ErrorCode.SUCCESS.value):
    query = _StubQueryService(rows, rt_cd=rt_cd)
    return OverseasFillReconcileService(
        trade_repository=repo, stock_query_service=query,
    ), query


async def test_fully_filled_lot_is_ok_and_untouched(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, order_no="0001234")
    service, _ = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "3"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data["counts"]["ok"] == 1
    assert repo.get_holds()[0]["qty"] == 3


async def test_unfilled_lot_is_canceled_when_applied(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, order_no="0001234")
    service, _ = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "0", "nccs_qty": "3"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["counts"]["unfilled"] == 1
    assert repo.get_holds() == []
    assert repo.get_all_trades()[0]["status"] == "CANCELED"


async def test_partial_fill_shrinks_lot_when_applied(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 5, order_no="0001234")
    service, _ = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "2"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["counts"]["partial"] == 1
    assert repo.get_holds()[0]["qty"] == 2


async def test_dry_run_reports_without_touching_ledger(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 5, order_no="0001234")
    service, _ = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "0"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813")

    assert resp.data["applied"] is False
    assert resp.data["counts"]["unfilled"] == 1
    assert resp.data["diffs"][0]["corrected"] is False
    assert repo.get_holds()[0]["qty"] == 5


async def test_lot_without_order_no_is_unknown_and_untouched(repo):
    """order_no 도입 이전 기록은 매칭 키가 없다 — 미체결로 단정하면 보유가 사라진다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3)
    service, _ = _service(repo, [{"odno": "0009999", "ft_ccld_qty": "3"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["counts"]["unknown"] == 1
    assert repo.get_holds()[0]["qty"] == 3


async def test_order_missing_from_history_is_unknown_not_unfilled(repo):
    """조회 구간 밖 주문은 '체결 0' 이 아니라 '판정 불가' 다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, order_no="0001234")
    service, _ = _service(repo, [{"odno": "0007777", "ft_ccld_qty": "1"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["counts"]["unknown"] == 1
    assert repo.get_holds()[0]["qty"] == 3


async def test_broker_failure_leaves_ledger_untouched(repo):
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, order_no="0001234")
    service, _ = _service(repo, [], rt_cd=ErrorCode.API_ERROR.value)

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.rt_cd == ErrorCode.API_ERROR.value
    assert repo.get_holds()[0]["qty"] == 3


async def test_only_requested_exchange_is_reconciled(repo):
    """NYSE lot 을 NASD 체결내역으로 판정하면 잘못된 취소가 난다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 3, order_no="0001234")
    repo.log_buy("BRK", OverseasExchange.NYSE, 400.0, 1, order_no="0005678")
    service, query = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "0"}])

    resp = await service.reconcile(
        start_date="20260813", end_date="20260813", exchange="NASD", apply=True,
    )

    assert resp.data["checked"] == 1
    assert query.calls[0]["exchange"] == "NASD"
    assert [h["symbol"] for h in repo.get_holds()] == ["BRK"]


async def test_partial_fill_aggregates_split_executions(repo):
    """한 주문이 여러 체결 행으로 쪼개져 오면 합산해야 한다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 5, order_no="0001234")
    service, _ = _service(repo, [
        {"odno": "0001234", "ft_ccld_qty": "2"},
        {"odno": "0001234", "ft_ccld_qty": "1"},
    ])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["counts"]["partial"] == 1
    assert repo.get_holds()[0]["qty"] == 3


async def test_overfilled_lot_is_ok_not_shrunk(repo):
    """체결이 원장보다 많으면(분할 lot 등) 줄이지 않는다 — 보유를 없앨 수 없다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 190.0, 2, order_no="0001234")
    service, _ = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "5"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["counts"]["ok"] == 1
    assert repo.get_holds()[0]["qty"] == 2


async def test_sold_lots_are_not_reconciled(repo):
    """청산된 lot 을 다시 대사하면 실현 성과가 취소로 뒤집힌다."""
    repo.log_buy("AAPL", OverseasExchange.NASD, 100.0, 1, order_no="0001234")
    repo.log_sell("AAPL", 110.0)
    service, _ = _service(repo, [{"odno": "0001234", "ft_ccld_qty": "0"}])

    resp = await service.reconcile(start_date="20260813", end_date="20260813", apply=True)

    assert resp.data["checked"] == 0
    assert repo.get_all_trades()[0]["status"] == "SOLD"


def test_filled_qty_by_order_sums_rows_and_skips_unusable_ones():
    from services.overseas_fill_reconcile_service import OverseasFillReconcileService as Svc

    totals = Svc._filled_qty_by_order({
        "output": [
            {"odno": "0001", "ft_ccld_qty": "3"},
            {"odno": "0001", "ft_ccld_qty": "2"},
            {"odno": "", "ft_ccld_qty": "9"},
            "행 아님",
            {"odno": "0002", "ft_ccld_qty": "수량아님"},
        ]
    })

    assert totals["0001"] == 5
    assert totals["0002"] == 0
    assert "" not in totals


def test_filled_qty_by_order_accepts_a_bare_row_list():
    from services.overseas_fill_reconcile_service import OverseasFillReconcileService as Svc

    assert Svc._filled_qty_by_order([{"odno": "0001", "ft_ccld_qty": "4"}]) == {"0001": 4}


@pytest.mark.parametrize("payload", [None, "문자열", 7, {"output": "리스트 아님"}])
def test_filled_qty_by_order_returns_empty_for_unusable_payloads(payload):
    from services.overseas_fill_reconcile_service import OverseasFillReconcileService as Svc

    assert Svc._filled_qty_by_order(payload) == {}


def test_first_key_returns_the_first_non_blank_value():
    from services.overseas_fill_reconcile_service import _first_key

    assert _first_key({"a": "", "b": "  ", "c": "값"}, ("a", "b", "c")) == "값"
    assert _first_key({"a": ""}, ("a", "missing")) == ""


@pytest.mark.parametrize(
    "raw, expected",
    [("1,050", 1050), ("3.9", 3), (None, 0), ("", 0), ("수량아님", 0), (object(), 0)],
)
def test_to_int_coercion(raw, expected):
    from services.overseas_fill_reconcile_service import _to_int

    assert _to_int(raw) == expected
