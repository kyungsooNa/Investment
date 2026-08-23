import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.opening_position_reconcile_service import OpeningPositionReconcileService


@pytest.mark.asyncio
async def test_opening_reconcile_delegates_to_virtual_trade_reconcile():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930", "hldg_qty": "1"}]},
        )
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={
            "force_closed": ["000660"],
            "unknown_in_broker": ["035420"],
            "quantity_mismatches": [{"code": "005930", "local_qty": 3, "broker_qty": 1}],
        }
    )
    virtual_trade_service.get_holds_by_strategy.return_value = []
    logger = MagicMock()
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        logger=logger,
    )

    result = await service.reconcile_once()

    virtual_trade_service.reconcile_with_broker.assert_awaited_once_with(
        [{"pdno": "005930", "hldg_qty": "1"}],
        logger=logger,
    )
    assert result == {
        "force_closed": ["000660"],
        "unknown_in_broker": ["035420"],
        "quantity_mismatches": [{"code": "005930", "local_qty": 3, "broker_qty": 1}],
        "mismatch_count": 3,
        "error": None,
        "stale_broker_reconciled": [],
    }


@pytest.mark.asyncio
async def test_opening_reconcile_reports_zero_mismatch_when_positions_match():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OK",
            data={"output1": [{"pdno": "005930", "hldg_qty": "3"}]},
        )
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={"force_closed": [], "unknown_in_broker": [], "quantity_mismatches": []}
    )
    virtual_trade_service.get_holds_by_strategy.return_value = []
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    assert result["force_closed"] == []
    assert result["unknown_in_broker"] == []
    assert result["quantity_mismatches"] == []
    assert result["mismatch_count"] == 0
    assert result["error"] is None
    assert result["stale_broker_reconciled"] == []


@pytest.mark.asyncio
async def test_opening_reconcile_reports_balance_failure_without_delegation():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="denied", data=None)
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock()
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    assert result == {
        "force_closed": [],
        "unknown_in_broker": [],
        "quantity_mismatches": [],
        "mismatch_count": 0,
        "error": "denied",
    }
    virtual_trade_service.reconcile_with_broker.assert_not_awaited()


@pytest.mark.asyncio
async def test_opening_reconcile_flags_broker_reconciled_holds_older_than_threshold():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={"force_closed": [], "unknown_in_broker": [], "quantity_mismatches": []}
    )
    virtual_trade_service.get_holds_by_strategy.return_value = [
        {"code": "006110", "buy_date": "2026-05-14 15:14:05"},  # 54일 경과 → stale
        {"code": "084370", "buy_date": "2026-07-02 09:13:53"},  # 5일 경과 → 임계값 미만
    ]
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 7, 7, 9, 5)
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        market_clock=market_clock,
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    virtual_trade_service.get_holds_by_strategy.assert_called_once_with("broker_reconciled")
    assert result["stale_broker_reconciled"] == [{"code": "006110", "days_held": 54}]


@pytest.mark.asyncio
async def test_opening_reconcile_blocks_force_close_when_paper_account_changes(tmp_path):
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds.return_value = [{"code": "005930", "strategy": "S1", "qty": 1}]
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={
            "force_closed": [],
            "force_close_blocked": ["005930"],
            "unknown_in_broker": [],
            "quantity_mismatches": [],
        }
    )
    virtual_trade_service.get_holds_by_strategy.return_value = []
    state_file = tmp_path / "paper_reconcile_state.json"
    state_file.write_text('{"paper_account_number": "OLD-123"}', encoding="utf-8")
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        is_paper_trading=True,
        paper_account_number="NEW-456",
        paper_account_rollover_state_file_path=str(state_file),
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    virtual_trade_service.reconcile_with_broker.assert_awaited_once_with(
        [],
        logger=service._logger,
        allow_force_close=False,
    )
    assert result["force_closed"] == []
    assert result["force_close_blocked"] == ["005930"]
    assert result["mismatch_count"] == 1
    assert result["paper_account_rollover"] == {
        "detected": True,
        "reason": "account_changed",
        "previous_account_number": "***-123",
        "current_account_number": "***-456",
        "local_hold_count": 1,
    }


@pytest.mark.asyncio
async def test_opening_reconcile_blocks_force_close_for_empty_paper_balance_without_prior_state(tmp_path):
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
    )
    virtual_trade_service = MagicMock()
    virtual_trade_service.get_holds.return_value = [{"code": "005930", "strategy": "S1", "qty": 1}]
    virtual_trade_service.reconcile_with_broker = AsyncMock(
        return_value={
            "force_closed": [],
            "force_close_blocked": ["005930"],
            "unknown_in_broker": [],
            "quantity_mismatches": [],
        }
    )
    virtual_trade_service.get_holds_by_strategy.return_value = []
    service = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=virtual_trade_service,
        is_paper_trading=True,
        paper_account_number="NEW-001",
        paper_account_rollover_state_file_path=str(tmp_path / "paper_reconcile_state.json"),
        logger=MagicMock(),
    )

    result = await service.reconcile_once()

    virtual_trade_service.reconcile_with_broker.assert_awaited_once_with(
        [],
        logger=service._logger,
        allow_force_close=False,
    )
    assert result["paper_account_rollover"]["reason"] == "empty_paper_balance_with_local_holds"


def _service(tmp_path=None, **kwargs):
    broker = kwargs.pop("broker", None)
    if broker is None:
        broker = MagicMock()
        broker.get_account_balance = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output1": []})
        )
    vts = kwargs.pop("virtual_trade_service", None)
    if vts is None:
        vts = MagicMock()
        vts.reconcile_with_broker = AsyncMock(return_value={})
        vts.get_holds_by_strategy.return_value = []
        vts.get_holds.return_value = []
    if tmp_path is not None:
        kwargs.setdefault(
            "paper_account_rollover_state_file_path", str(tmp_path / "state.json")
        )
    return OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        logger=MagicMock(),
        **kwargs,
    ), broker, vts


@pytest.mark.asyncio
async def test_reconcile_records_api_failure_on_kill_switch():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="잔고 실패", data=None)
    )
    kill_switch = MagicMock()
    kill_switch.record_api_failure = AsyncMock()
    service, _, vts = _service(broker=broker, kill_switch_service=kill_switch)

    result = await service.reconcile_once()

    kill_switch.record_api_failure.assert_awaited_once()
    assert "잔고 실패" in kill_switch.record_api_failure.await_args.args[0]
    assert result["error"] == "잔고 실패"
    vts.reconcile_with_broker.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_handles_non_dict_balance_payload():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data="문자열")
    )
    service, _, vts = _service(broker=broker)

    await service.reconcile_once()

    vts.reconcile_with_broker.assert_awaited_once()
    assert vts.reconcile_with_broker.await_args.args[0] == []


@pytest.mark.asyncio
async def test_reconcile_saves_paper_rollover_state_when_no_rollover(tmp_path):
    state_file = tmp_path / "state.json"
    service, _, _ = _service(
        is_paper_trading=True,
        paper_account_number="50202454",
        paper_account_rollover_state_file_path=str(state_file),
        market_clock=MagicMock(get_current_kst_time=MagicMock(return_value=datetime(2026, 8, 20, 9, 5))),
    )

    await service.reconcile_once()

    assert json.loads(state_file.read_text(encoding="utf-8"))["paper_account_number"] == "50202454"


@pytest.mark.parametrize(
    "account, expected",
    [(None, None), ("", None), ("1234", "****"), ("50202454", "****2454")],
)
def test_account_number_masking(account, expected):
    assert OpeningPositionReconcileService._mask_account_number(account) == expected


@pytest.mark.parametrize(
    "holdings, expected",
    [
        ([], False),
        (None, False),
        (["문자열"], False),
        ([{"hldg_qty": "0"}], False),
        ([{"hldg_qty": ""}], False),
        ([{"hldg_qty": "없음"}], False),
        ([{"hldg_qty": "1,200"}], True),
        ([{"HLDG_QTY": "5"}], True),
        ([{"qty": "3"}], True),
        ([{"quantity": "7"}], True),
    ],
)
def test_positive_broker_holding_detection(holdings, expected):
    assert OpeningPositionReconcileService._has_positive_broker_holding(holdings) is expected


def test_rollover_detection_requires_paper_mode_and_local_holds(tmp_path):
    service, _, vts = _service(tmp_path)

    # 실전 모드에서는 판정하지 않는다.
    assert service._detect_paper_account_rollover([]) == (False, None)

    service._is_paper_trading = True
    service._paper_account_number = "50202454"
    vts.get_holds.return_value = []
    assert service._detect_paper_account_rollover([]) == (False, None)

    # 브로커에 실제 보유가 있으면 rollover 가 아니다.
    vts.get_holds.return_value = [{"code": "005930"}]
    assert service._detect_paper_account_rollover([{"hldg_qty": "5"}]) == (False, None)


def test_rollover_detection_is_disabled_by_flag(tmp_path):
    service, _, vts = _service(
        tmp_path,
        is_paper_trading=True,
        paper_account_number="50202454",
        block_force_close_on_paper_rollover=False,
    )
    vts.get_holds.return_value = [{"code": "005930"}]

    assert service._detect_paper_account_rollover([]) == (False, None)


def test_rollover_is_not_flagged_when_account_matches_saved_state(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"paper_account_number": "50202454"}), encoding="utf-8")
    service, _, vts = _service(
        is_paper_trading=True,
        paper_account_number="50202454",
        paper_account_rollover_state_file_path=str(state_file),
    )
    vts.get_holds.return_value = [{"code": "005930"}]

    assert service._detect_paper_account_rollover([]) == (False, None)


def test_rollover_state_load_tolerates_missing_and_broken_files(tmp_path):
    service, _, _ = _service(tmp_path)
    assert service._load_paper_rollover_state() == {}

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    service._paper_account_rollover_state_file = broken
    assert service._load_paper_rollover_state() == {}
    service._logger.warning.assert_called_once()

    not_a_dict = tmp_path / "list.json"
    not_a_dict.write_text("[1, 2]", encoding="utf-8")
    service._paper_account_rollover_state_file = not_a_dict
    assert service._load_paper_rollover_state() == {}


def test_rollover_state_save_failure_is_logged(tmp_path):
    service, _, _ = _service(tmp_path)
    service._paper_account_rollover_state_file = MagicMock()
    service._paper_account_rollover_state_file.parent.mkdir = MagicMock(
        side_effect=OSError("디렉터리 생성 실패")
    )

    service._save_paper_rollover_state("50202454")

    service._logger.warning.assert_called_once()


def test_stale_broker_reconciled_skips_rows_without_code_or_parsable_date(tmp_path):
    service, _, vts = _service(
        tmp_path,
        market_clock=MagicMock(get_current_kst_time=MagicMock(return_value=datetime(2026, 8, 20))),
    )
    vts.get_holds_by_strategy.return_value = [
        {"code": "", "buy_date": "2026-08-01 09:00:00"},
        {"code": "005930", "buy_date": ""},
        {"code": "000660", "buy_date": "날짜아님"},
        {"code": "035420", "buy_date": "2026-08-19 09:00:00"},
        {"code": "005380", "buy_date": "2026-08-01 09:00:00"},
    ]

    assert service._get_stale_broker_reconciled() == [{"code": "005380", "days_held": 19}]


def test_current_time_falls_back_to_system_clock_without_market_clock(tmp_path):
    service, _, _ = _service(tmp_path)

    assert service._current_kst_time().tzinfo is not None


def test_success_response_detection_accepts_duck_typed_objects():
    assert OpeningPositionReconcileService._is_success_response(
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None)
    ) is True
    assert OpeningPositionReconcileService._is_success_response(
        MagicMock(rt_cd=ErrorCode.SUCCESS.value)
    ) is True
    assert OpeningPositionReconcileService._is_success_response(object()) is False


@pytest.mark.asyncio
async def test_call_retries_without_keyword_when_method_rejects_it():
    def only_positional(value):
        return f"got {value}"

    assert await OpeningPositionReconcileService._call(only_positional, "A", exchange="KRX") == "got A"

    async def awaitable(value):
        return f"async {value}"

    assert await OpeningPositionReconcileService._call(awaitable, "B") == "async B"
