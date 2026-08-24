"""system 라우트의 운영 상태·프로세스 제어 방어 경로 테스트.

`/api/system/operations/status` 는 부가 서비스가 없거나 예외를 던져도 요약을
돌려줘야 한다. 여기서는 각 조회처가 미배선/예외일 때의 기본값과, 종료·재시작
예약처럼 타이머로 감싼 경로를 채운다.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from view.web.routes import system as mod


def _blank_ctx():
    """부가 서비스가 하나도 배선되지 않은 컨텍스트."""
    return SimpleNamespace(
        scheduler=None, order_execution_service=None, virtual_trade_service=None,
        account_snapshot_cache=None, websocket_watchdog_task=None,
        notification_service=None, kill_switch_service=None,
        after_market_reconcile_task=None, stock_query_service=None,
        api_budget_limiter=None, data_quality_service=None,
    )


# --- cron 트리거 정규화 -------------------------------------------------------

@pytest.mark.parametrize(
    "task",
    [
        None,
        SimpleNamespace(_loop_timezone=None, _loop_cron_hour=15, _loop_cron_minute=40),
        SimpleNamespace(_loop_timezone="Asia/Seoul", _loop_cron_hour="15",
                        _loop_cron_minute=40),
        SimpleNamespace(_loop_timezone="Asia/Seoul", _loop_cron_hour=15,
                        _loop_cron_minute=None),
    ],
)
def test_incomplete_cron_metadata_yields_no_trigger(task):
    assert mod._task_trigger_info(task) is None


def test_complete_cron_metadata_is_normalized():
    task = SimpleNamespace(_loop_timezone="Asia/Seoul", _loop_cron_hour=15,
                           _loop_cron_minute=40)

    assert mod._task_trigger_info(task) == {
        "timezone": "Asia/Seoul", "hour": 15, "minute": 40
    }


# --- 숫자 변환 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [(None, 0.0), ("", 0.0), ("N/A", 0.0), ("1,050", 1050.0), ("숫자아님", 0.0), (3, 3.0)],
)
def test_float_coercion(raw, expected):
    assert mod._to_float(raw) == expected


# --- 운영 손익 요약 -----------------------------------------------------------

def test_pnl_summary_is_all_blank_without_any_wired_service():
    pnl = mod._build_operations_pnl(_blank_ctx())

    assert pnl["realized"] == {"summary": None, "realized_pnl_won": None, "sold_count": 0}
    assert pnl["evaluation"]["broker_total_equity"] is None
    assert pnl["day"] == {"current_return_pct": None, "daily_change_pct": None,
                          "baseline_date": None}


def test_summary_falls_back_to_the_no_argument_call_on_a_type_error():
    vts = MagicMock()
    vts.get_summary.side_effect = [TypeError("apply_cost 미지원"), {"total_trades": 3}]
    vts.get_all_trades.return_value = []
    vts.get_holds.return_value = []
    vts._load_data.return_value = {}
    ctx = _blank_ctx()
    ctx.virtual_trade_service = vts

    assert mod._build_operations_pnl(ctx)["realized"]["summary"] == {"total_trades": 3}


def test_summary_is_none_when_both_call_shapes_fail():
    vts = MagicMock()
    vts.get_summary.side_effect = [TypeError("apply_cost 미지원"), RuntimeError("db")]
    vts.get_all_trades.side_effect = RuntimeError("db")
    vts.get_holds.side_effect = RuntimeError("db")
    vts._load_data.side_effect = RuntimeError("db")
    ctx = _blank_ctx()
    ctx.virtual_trade_service = vts

    pnl = mod._build_operations_pnl(ctx)

    assert pnl["realized"]["summary"] is None
    assert pnl["realized"]["realized_pnl_won"] is None
    assert pnl["evaluation"]["virtual_holding_buy_amount"] is None


def test_summary_is_none_when_the_first_call_fails_outright():
    vts = MagicMock()
    vts.get_summary.side_effect = RuntimeError("db")
    vts.get_all_trades.return_value = []
    vts.get_holds.return_value = []
    vts._load_data.return_value = {}
    ctx = _blank_ctx()
    ctx.virtual_trade_service = vts

    assert mod._build_operations_pnl(ctx)["realized"]["summary"] is None


def test_realized_and_holding_amounts_are_summed_from_the_ledger():
    vts = MagicMock()
    vts.get_summary.return_value = {}
    vts.get_all_trades.return_value = [
        {"status": "SOLD", "buy_price": "70,000", "sell_price": "75,000", "qty": "10"},
        {"status": "HOLD", "buy_price": "60,000", "qty": "5"},
    ]
    vts.get_holds.return_value = [{"buy_price": "60,000", "qty": "5"}]
    vts._load_data.return_value = {
        "daily": {"2026-05-01": {"ALL": 1.0}, "2026-05-04": {"ALL": 3.5}}
    }
    vts.get_daily_change.return_value = (2.5, "2026-05-01")
    ctx = _blank_ctx()
    ctx.virtual_trade_service = vts

    pnl = mod._build_operations_pnl(ctx)

    assert pnl["realized"] == {"summary": {}, "realized_pnl_won": 50000, "sold_count": 1}
    assert pnl["evaluation"]["virtual_holding_buy_amount"] == 300000
    assert pnl["day"] == {"current_return_pct": 3.5, "daily_change_pct": 2.5,
                          "baseline_date": "2026-05-01"}


def test_broker_snapshot_fills_the_evaluation_block_and_unrealized_pnl():
    vts = MagicMock()
    vts.get_summary.return_value = {}
    vts.get_all_trades.return_value = []
    vts.get_holds.return_value = [{"buy_price": "60000", "qty": "5"}]
    vts._load_data.return_value = {}
    ctx = _blank_ctx()
    ctx.virtual_trade_service = vts
    ctx.account_snapshot_cache = SimpleNamespace(_snapshot=SimpleNamespace(
        positions={"005930": 400000}, total_equity=1_000_000,
        available_cash=600_000, fetched_at=datetime(2026, 5, 4, 15, 30),
    ))

    evaluation = mod._build_operations_pnl(ctx)["evaluation"]

    assert evaluation["broker_total_equity"] == 1_000_000
    assert evaluation["broker_position_count"] == 1
    assert evaluation["estimated_unrealized_pnl_won"] == 100000
    assert evaluation["snapshot_fetched_at"] == "2026-05-04T15:30:00"


def test_a_broken_broker_snapshot_leaves_the_evaluation_block_blank():
    ctx = _blank_ctx()
    snapshot = MagicMock()
    type(snapshot).positions = property(lambda self: (_ for _ in ()).throw(RuntimeError("깨짐")))
    ctx.account_snapshot_cache = SimpleNamespace(_snapshot=snapshot)

    assert mod._build_operations_pnl(ctx)["evaluation"]["broker_total_equity"] is None


# --- /system/operations/status ------------------------------------------------

def test_operations_status_survives_every_service_raising(web_client, mock_web_ctx):
    mock_web_ctx.scheduler.get_status.side_effect = RuntimeError("스케줄러 오류")
    mock_web_ctx.order_execution_service.get_active_order_summary.side_effect = (
        RuntimeError("주문 오류")
    )
    mock_web_ctx.websocket_watchdog_task.get_progress.side_effect = RuntimeError("ws")
    mock_web_ctx.notification_service.external_handler_queue.qsize.side_effect = (
        RuntimeError("큐")
    )
    mock_web_ctx.kill_switch_service.get_status.side_effect = RuntimeError("ks")
    mock_web_ctx.after_market_reconcile_task.get_progress.side_effect = RuntimeError("rc")
    mock_web_ctx.api_budget_limiter.snapshot.side_effect = RuntimeError("budget")

    data = web_client.get("/api/system/operations/status").json()["data"]

    assert data["active_strategy_count"] == 0
    assert data["orders"]["active_order_count"] == 0
    assert data["kill_switch"] is None
    assert data["after_market_reconcile"] is None
    assert data["api_budget"] is None


def test_operations_status_ignores_non_dict_payloads(web_client, mock_web_ctx):
    mock_web_ctx.scheduler.get_status.return_value = "딕셔너리 아님"
    mock_web_ctx.order_execution_service.get_active_order_summary.return_value = []
    mock_web_ctx.websocket_watchdog_task.get_progress.return_value = None
    mock_web_ctx.api_budget_limiter.snapshot.return_value = "딕셔너리 아님"
    mock_web_ctx.stock_query_service._price_lookup_stats = "딕셔너리 아님"

    data = web_client.get("/api/system/operations/status").json()["data"]

    assert data["active_strategy_count"] == 0
    assert data["api_budget"] is None
    assert data["price_lookup"] is None


# --- 이력 조회 라우트 ---------------------------------------------------------

def test_reconcile_history_falls_back_to_an_empty_list_on_error(web_client, mock_web_ctx):
    mock_web_ctx.after_market_reconcile_task.get_history.side_effect = RuntimeError("db")

    assert web_client.get("/api/system/reconcile/history").json() == {
        "success": True, "data": []
    }


def test_reconcile_history_returns_the_task_history(web_client, mock_web_ctx):
    mock_web_ctx.after_market_reconcile_task.get_history.return_value = [{"ok": True}]

    body = web_client.get("/api/system/reconcile/history?count=5").json()

    assert body["data"] == [{"ok": True}]
    mock_web_ctx.after_market_reconcile_task.get_history.assert_called_once_with(count=5)


def test_data_quality_history_falls_back_to_an_empty_list_on_error(web_client, mock_web_ctx):
    mock_web_ctx.data_quality_service.get_violation_history.side_effect = RuntimeError("db")

    assert web_client.get("/api/system/data-quality/history").json() == {
        "success": True, "data": []
    }


# --- 프로세스 종료 / 재시작 ---------------------------------------------------

def test_shutdown_schedules_a_delayed_termination(web_client, mocker):
    timer = mocker.patch("view.web.routes.system.threading.Timer")

    assert web_client.post("/api/system/shutdown").json()["success"] is True

    timer.assert_called_once_with(mod._SHUTDOWN_DELAY_SEC, mod._terminate_process)
    timer.return_value.start.assert_called_once()


def test_restart_schedules_a_delayed_restart(web_client, mocker):
    timer = mocker.patch("view.web.routes.system.threading.Timer")

    assert web_client.post("/api/system/restart").json()["success"] is True

    timer.assert_called_once_with(mod._SHUTDOWN_DELAY_SEC, mod._restart_process)


def test_restart_keeps_the_current_process_when_the_spawn_fails(mocker):
    mocker.patch("view.web.routes.system._spawn_restarted_process",
                 side_effect=OSError("실행 불가"))
    terminate = mocker.patch("view.web.routes.system._terminate_process")

    mod._restart_process()

    terminate.assert_not_called()


def test_restart_terminates_the_current_process_after_a_successful_spawn(mocker):
    mocker.patch("view.web.routes.system._spawn_restarted_process")
    terminate = mocker.patch("view.web.routes.system._terminate_process")

    mod._restart_process()

    terminate.assert_called_once()


@pytest.mark.parametrize("os_name, expected_key", [("nt", "creationflags"),
                                                   ("posix", "start_new_session")])
def test_restarted_process_is_detached_from_the_current_one(mocker, os_name, expected_key):
    mocker.patch("view.web.routes.system.os.name", os_name)
    mocker.patch("view.web.routes.system.subprocess.CREATE_NEW_CONSOLE", 16, create=True)
    popen = mocker.patch("view.web.routes.system.subprocess.Popen")

    mod._spawn_restarted_process()

    assert expected_key in popen.call_args.kwargs


# --- 프로그램매매 모니터 상태 --------------------------------------------------

def test_program_trading_monitor_is_not_appended_twice(mock_web_ctx):
    result = [{"name": "program_trading_monitor"}]

    mod._append_program_trading_monitor_status(mock_web_ctx, result)

    assert len(result) == 1


def test_program_trading_monitor_is_skipped_when_the_service_is_unwired(mock_web_ctx):
    mock_web_ctx.program_trading_stream_service = None
    result = []

    mod._append_program_trading_monitor_status(mock_web_ctx, result)

    assert result == []


# --- 자금 한도 ---------------------------------------------------------------

class _ConfigWithSections(dict):
    """auth 등 dict 접근은 그대로 두고 속성 섹션만 덧붙인 full_config."""


def _attach_sizing_sections(ctx, risk_gate, position_sizing):
    config = _ConfigWithSections(ctx.full_config)
    config.risk_gate = risk_gate
    config.position_sizing = position_sizing
    ctx.full_config = config

def test_position_sizing_limits_report_none_without_configured_sections(web_client):
    """기본 full_config 에는 risk_gate/position_sizing 섹션이 없다."""
    body = web_client.get("/api/position-sizing/limits").json()

    assert body["max_order_amount_won"] is None
    assert body["max_per_position_pct"] is None
    assert body["defaults"]["max_order_amount_won"] is not None


def test_position_sizing_limits_update_writes_both_sections_and_persists(
    web_client, mock_web_ctx
):
    risk_gate = MagicMock(max_order_amount_won=1_000_000)
    position_sizing = MagicMock(max_per_position_pct=10.0)
    _attach_sizing_sections(mock_web_ctx, risk_gate, position_sizing)

    body = web_client.post(
        "/api/position-sizing/limits",
        json={"max_order_amount_won": 2_000_000, "max_per_position_pct": 20.0},
    ).json()

    assert body["success"] is True
    assert risk_gate.max_order_amount_won == 2_000_000
    assert position_sizing.max_per_position_pct == 20.0
    mock_web_ctx.save_position_sizing_state.assert_called_once()


def test_position_sizing_limits_update_skips_unsent_fields(web_client, mock_web_ctx):
    risk_gate = MagicMock(max_order_amount_won=1_000_000)
    position_sizing = MagicMock(max_per_position_pct=10.0)
    _attach_sizing_sections(mock_web_ctx, risk_gate, position_sizing)

    web_client.post("/api/position-sizing/limits", json={})

    assert risk_gate.max_order_amount_won == 1_000_000
    assert position_sizing.max_per_position_pct == 10.0
