"""StrategyScheduler 의 강제청산 재시도·보조 헬퍼 경로 테스트.

강제청산 주문이 로컬 서킷브레이커에만 막힌 경우, 스케줄러는 해제 시각 뒤에
미체결 매도와 실제 잔고를 다시 확인하고 나서만 재주문한다. 여기서는 그 예약/
재시도 분기와, 주변 파싱·상태 정리 헬퍼의 방어 경로를 채운다.
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, ResCommonResponse, TradeSignal
from scheduler.strategy_scheduler import (
    SignalRecord,
    StrategyScheduler,
    StrategySchedulerConfig,
)

NOW = datetime(2026, 5, 4, 13, 0, 0)
CLOSE = datetime(2026, 5, 4, 15, 30, 0)


def _scheduler(**attrs):
    """조립을 건너뛴 스케줄러에 테스트가 쓰는 속성만 채운다."""
    scheduler = StrategyScheduler.__new__(StrategyScheduler)
    scheduler._logger = MagicMock()
    scheduler._running = True
    scheduler._signal_history = []
    scheduler._strategies = []
    scheduler._force_exit_retry_tasks = {}
    tm = MagicMock()
    tm.get_current_kst_time.return_value = NOW
    tm.get_market_close_time.return_value = CLOSE
    tm.async_sleep = AsyncMock()
    scheduler._tm = tm
    scheduler._oes = MagicMock(broker_api_wrapper=AsyncMock())
    scheduler._get_order_cutoff_time = MagicMock(
        return_value=CLOSE - timedelta(minutes=10)
    )
    for key, value in attrs.items():
        setattr(scheduler, key, value)
    return scheduler


def _signal(**overrides):
    base = dict(strategy_name="모멘텀", code="005930", name="삼성전자", action="SELL",
                price=70000, qty=10, reason="전략 종료 강제 청산 (시장가)")
    base.update(overrides)
    return TradeSignal(**base)


# --- 서킷브레이커 응답 판별 ----------------------------------------------------

@pytest.mark.parametrize(
    "signal_kwargs, msg1, expected",
    [
        ({}, "서킷 브레이커 개방 — 3분 후 재시도", True),
        ({"action": "BUY"}, "서킷 브레이커 개방", False),
        ({"reason": "익절"}, "서킷 브레이커 개방", False),
        ({}, "주문가능금액 부족", False),
    ],
)
def test_circuit_breaker_response_detection(signal_kwargs, msg1, expected):
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1=msg1, data=None)

    assert StrategyScheduler._is_force_exit_circuit_breaker_response(
        _signal(**signal_kwargs), resp
    ) is expected


def test_circuit_breaker_detection_is_false_without_a_response():
    assert StrategyScheduler._is_force_exit_circuit_breaker_response(_signal(), None) is False


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"retry_after_seconds": 90}, 90),
        ({"retry_after_seconds": 0}, 1),
        ({"retry_after_seconds": "숫자아님"}, 300),
        ({}, 1),   # 키가 없으면 0 → 최소 1초
        (None, 300),
    ],
)
def test_retry_delay_falls_back_to_five_minutes(data, expected):
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="", data=data)

    assert StrategyScheduler._force_exit_retry_delay_sec(resp) == expected


# --- 재시도 예약 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_is_not_scheduled_past_the_order_cutoff():
    scheduler = _scheduler()
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="",
                             data={"retry_after_seconds": 60 * 60 * 5})

    assert scheduler._schedule_force_exit_circuit_breaker_retry(_signal(), resp) is False
    assert scheduler._force_exit_retry_tasks == {}
    scheduler._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_a_second_retry_for_the_same_code_is_not_scheduled():
    scheduler = _scheduler()
    pending = MagicMock(done=MagicMock(return_value=False))
    scheduler._force_exit_retry_tasks["모멘텀:005930"] = pending
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="",
                             data={"retry_after_seconds": 60})

    assert scheduler._schedule_force_exit_circuit_breaker_retry(_signal(), resp) is True
    assert scheduler._force_exit_retry_tasks["모멘텀:005930"] is pending


@pytest.mark.asyncio
async def test_a_scheduled_retry_registers_and_clears_itself():
    scheduler = _scheduler()
    scheduler._retry_force_exit_after_circuit_breaker = AsyncMock()
    resp = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="",
                             data={"retry_after_seconds": 60})

    assert scheduler._schedule_force_exit_circuit_breaker_retry(_signal(), resp) is True
    task = scheduler._force_exit_retry_tasks["모멘텀:005930"]
    await task

    await asyncio.sleep(0)
    assert scheduler._force_exit_retry_tasks == {}


# --- 재시도 실행 -------------------------------------------------------------

def _unfilled(rows):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={"output": rows})


@pytest.mark.asyncio
async def test_retry_is_abandoned_once_the_scheduler_stops():
    scheduler = _scheduler(_running=False)

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_is_abandoned_past_the_order_cutoff():
    scheduler = _scheduler()
    scheduler._get_order_cutoff_time = MagicMock(return_value=NOW - timedelta(minutes=1))

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_retry_is_abandoned_without_a_broker():
    scheduler = _scheduler(_oes=MagicMock(broker_api_wrapper=None))

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_retry_is_abandoned_when_the_unfilled_lookup_fails():
    scheduler = _scheduler()
    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders = AsyncMock(return_value=None)

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_retry_is_skipped_when_a_sell_order_is_already_pending():
    scheduler = _scheduler()
    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders = AsyncMock(
        return_value=_unfilled([{"pdno": "005930", "sll_buy_dvsn_cd": "01"}])
    )
    scheduler._execute_signal = AsyncMock()

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._execute_signal.assert_not_awaited()
    scheduler._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_retry_is_abandoned_when_the_balance_lookup_fails():
    scheduler = _scheduler()
    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders = AsyncMock(
        return_value=_unfilled([])
    )
    scheduler._get_broker_position_map_for_force_exit = AsyncMock(return_value=None)

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_retry_is_skipped_when_the_broker_holds_nothing():
    scheduler = _scheduler()
    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders = AsyncMock(
        return_value=_unfilled([])
    )
    scheduler._get_broker_position_map_for_force_exit = AsyncMock(return_value={})
    scheduler._execute_signal = AsyncMock()

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._execute_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_submits_a_market_sell_for_the_broker_quantity():
    scheduler = _scheduler()
    scheduler._force_exit_retry_tasks["k"] = MagicMock()
    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders = AsyncMock(
        return_value=_unfilled([])
    )
    scheduler._get_broker_position_map_for_force_exit = AsyncMock(
        return_value={"005930": 7}
    )
    scheduler._execute_signal = AsyncMock()

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    retry_signal = scheduler._execute_signal.await_args.args[0]
    assert retry_signal.action == "SELL"
    assert retry_signal.price == 0
    assert retry_signal.qty == 7
    assert scheduler._force_exit_retry_tasks == {}


@pytest.mark.asyncio
async def test_retry_cancellation_is_logged_and_propagated():
    scheduler = _scheduler()
    scheduler._tm.async_sleep = AsyncMock(side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_retry_exceptions_are_logged_and_swallowed():
    scheduler = _scheduler()
    scheduler._oes.broker_api_wrapper.inquire_unfilled_orders = AsyncMock(
        side_effect=RuntimeError("API 오류")
    )

    await scheduler._retry_force_exit_after_circuit_breaker("k", _signal(), 1)

    scheduler._logger.exception.assert_called_once()


# --- 미체결 매도 판별 ----------------------------------------------------------

@pytest.mark.parametrize(
    "data, expected",
    [
        ("딕셔너리 아님", False),
        ({"output": [{"pdno": "005930", "sll_buy_dvsn_cd": "01"}]}, True),
        ({"output1": {"PDNO": "005930", "SLL_BUY_DVSN_CD": "01"}}, True),
        ({"output": [{"pdno": "005930", "sll_buy_dvsn_cd": "02"}]}, False),
        ({"output": [{"pdno": "000660", "sll_buy_dvsn_cd": "01"}]}, False),
        ({"output": ["행이 dict 가 아님"]}, False),
        ({}, False),
    ],
)
def test_unfilled_sell_detection(data, expected):
    assert StrategyScheduler._has_broker_unfilled_sell(data, "005930") is expected


# --- 잔고 파싱 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected", [("1,050", 1050), ("  7 ", 7), ("숫자아님", 0), (None, 0)]
)
def test_position_quantity_parsing(raw, expected):
    assert StrategyScheduler._parse_position_qty(raw) == expected


def test_position_map_merges_duplicate_codes_and_drops_empty_rows():
    positions = StrategyScheduler._normalize_broker_position_map([
        "행이 dict 가 아님",
        {"pdno": "", "hldg_qty": "5"},
        {"pdno": "005930", "hldg_qty": "5"},
        {"PDNO": "005930", "HLDG_QTY": "3"},
        {"code": "000660", "qty": "0"},
    ])

    assert positions == {"005930": 8}


@pytest.mark.asyncio
async def test_broker_position_map_is_none_without_a_broker():
    scheduler = _scheduler(_oes=MagicMock(broker_api_wrapper=None))

    assert await scheduler._get_broker_position_map_for_force_exit() is None


@pytest.mark.asyncio
async def test_broker_position_map_is_none_when_the_lookup_raises():
    scheduler = _scheduler()
    scheduler._oes.broker_api_wrapper.get_account_balance = AsyncMock(
        side_effect=RuntimeError("API 오류")
    )

    assert await scheduler._get_broker_position_map_for_force_exit() is None
    scheduler._logger.warning.assert_called_once()


# --- 활성 매수 주문 판별 -------------------------------------------------------

def test_active_buy_order_probe_is_false_without_the_lookup():
    scheduler = _scheduler(_oes=MagicMock(get_order_context=None))

    assert scheduler._has_active_buy_order_for_force_exit("005930") is False


def test_active_buy_order_probe_retries_the_legacy_two_argument_signature():
    context = SimpleNamespace(state=SimpleNamespace(is_terminal=False))
    getter = MagicMock(side_effect=[TypeError("exchange 미지원"), context])
    scheduler = _scheduler(_oes=MagicMock(get_order_context=getter))

    assert scheduler._has_active_buy_order_for_force_exit("005930") is True
    assert getter.call_count == 2


def test_active_buy_order_probe_swallows_lookup_errors():
    scheduler = _scheduler(
        _oes=MagicMock(get_order_context=MagicMock(side_effect=RuntimeError("오류")))
    )

    assert scheduler._has_active_buy_order_for_force_exit("005930") is False
    scheduler._logger.warning.assert_called_once()


@pytest.mark.parametrize(
    "context, expected",
    [
        (None, False),
        (SimpleNamespace(state=SimpleNamespace(is_terminal=True)), False),
        (SimpleNamespace(state=SimpleNamespace(is_terminal=False)), True),
    ],
)
def test_active_buy_order_probe_follows_the_order_state(context, expected):
    scheduler = _scheduler(
        _oes=MagicMock(get_order_context=MagicMock(return_value=context))
    )

    assert scheduler._has_active_buy_order_for_force_exit("005930") is expected


# --- 최우선 매수호가 추출 -------------------------------------------------------

@pytest.mark.parametrize(
    "data, expected",
    [
        ("딕셔너리 아님", 0),
        ({"bidp1": "70000"}, 70000),
        ({"output1": {"bidp1": "70000"}}, 70000),
        ({"output": {"bidp1": "70000"}}, 70000),
        ({"bidp1": "숫자아님"}, 0),
        ({"bidp1": "0"}, 0),
        ({}, 0),
    ],
)
def test_best_bid_extraction(data, expected):
    assert StrategyScheduler._extract_best_bid(data) == expected


# --- 신호 이력 조회 -----------------------------------------------------------

def _record(action, qty, *, success=True, timestamp="2026-05-04 09:30:00", price=70000):
    return SignalRecord(
        timestamp=timestamp, strategy_name="모멘텀", code="005930", name="삼성전자",
        action=action, price=price, qty=qty, reason="", api_success=success,
    )


def test_net_quantity_skips_failed_and_off_date_records():
    scheduler = _scheduler(_signal_history=[
        _record("BUY", 10),
        _record("BUY", 5, success=False),
        _record("BUY", 3, timestamp="2026-05-01 09:30:00"),
        _record("SELL", 4),
    ])

    assert scheduler._get_signal_net_qty(
        "모멘텀", "005930", date_prefix="2026-05-04"
    ) == 6


def test_latest_open_buy_record_is_the_one_the_sells_do_not_cover():
    scheduler = _scheduler(_signal_history=[
        _record("BUY", 10, price=70000),
        _record("BUY", 5, price=71000),
        _record("SELL", 5),
    ])

    record = scheduler._get_latest_open_buy_record("모멘텀", "005930")

    assert record.price == 70000


def test_latest_open_buy_record_is_none_once_everything_is_sold():
    scheduler = _scheduler(_signal_history=[_record("BUY", 10), _record("SELL", 10)])

    assert scheduler._get_latest_open_buy_record("모멘텀", "005930") is None


@pytest.mark.parametrize("code, expected", [("005930", True), ("5930", False),
                                            ("00593A", False)])
def test_strategy_code_validation(code, expected):
    assert StrategyScheduler._is_valid_strategy_code(code) is expected


def test_signal_date_prefix_is_blank_when_the_clock_fails():
    scheduler = _scheduler()
    scheduler._tm.get_current_kst_time.side_effect = RuntimeError("시계 오류")

    assert scheduler._current_signal_date_prefix() == ""


def test_a_blank_date_prefix_matches_every_record():
    assert StrategyScheduler._signal_record_on_date(_record("BUY", 1), "") is True


# --- 전략 state 정리 ----------------------------------------------------------

def test_position_state_is_empty_for_a_strategy_without_one():
    scheduler = _scheduler()

    assert scheduler._get_strategy_position_state(SimpleNamespace()) == {}
    assert scheduler._get_strategy_position_state(
        SimpleNamespace(position_state="dict 아님")
    ) == {}


def test_persisting_state_is_a_no_op_without_the_hook():
    scheduler = _scheduler()

    scheduler._persist_strategy_position_state(SimpleNamespace(persist_state=None))


def test_persisting_state_failure_is_logged():
    scheduler = _scheduler()
    strategy = SimpleNamespace(
        name="모멘텀", persist_state=MagicMock(side_effect=RuntimeError("디스크"))
    )

    scheduler._persist_strategy_position_state(strategy)

    scheduler._logger.warning.assert_called_once()


def test_reconciled_state_clearing_is_a_no_op_without_codes():
    scheduler = _scheduler()

    scheduler._clear_reconciled_position_state(["", "  "])

    scheduler._logger.warning.assert_not_called()


def test_reconciled_state_clearing_drops_only_the_named_codes():
    strategy = SimpleNamespace(
        name="모멘텀", position_state={"005930": object(), "000660": object()},
        persist_state=MagicMock(),
    )
    scheduler = _scheduler(_strategies=[
        StrategySchedulerConfig(strategy=strategy),
    ])

    scheduler._clear_reconciled_position_state(["005930"])

    assert list(strategy.position_state) == ["000660"]
    strategy.persist_state.assert_called_once()


def test_force_exit_state_clearing_is_skipped_for_a_swing_strategy():
    cfg = StrategySchedulerConfig(
        strategy=SimpleNamespace(name="스윙", position_state={"005930": object()}),
        force_exit_on_close=False,
    )

    assert _scheduler()._clear_force_exit_position_state(cfg) is False


def test_force_exit_state_clearing_is_skipped_when_nothing_is_held():
    cfg = StrategySchedulerConfig(
        strategy=SimpleNamespace(name="데이", position_state={}),
        force_exit_on_close=True,
    )

    assert _scheduler()._clear_force_exit_position_state(cfg) is False


def test_force_exit_state_clearing_empties_the_position_state():
    strategy = SimpleNamespace(name="데이", position_state={"005930": object()},
                               persist_state=MagicMock())
    cfg = StrategySchedulerConfig(strategy=strategy, force_exit_on_close=True)

    assert _scheduler()._clear_force_exit_position_state(cfg) is True
    assert strategy.position_state == {}


# --- 마지막 실행 시각 저장/복원 --------------------------------------------------

def test_last_run_persist_failure_is_logged():
    scheduler = _scheduler(_store=MagicMock(
        save_keyed=MagicMock(side_effect=RuntimeError("db"))
    ))

    scheduler._persist_last_run("모멘텀", NOW)

    scheduler._logger.warning.assert_called_once()


@pytest.mark.parametrize("raw", [None, "", 123, "날짜아님"])
def test_last_run_restore_ignores_unusable_values(raw):
    scheduler = _scheduler(_store=MagicMock(load_keyed=MagicMock(return_value=raw)),
                           _last_run={})

    scheduler._restore_last_run("모멘텀", NOW)

    assert scheduler._last_run == {}


def test_last_run_restore_only_accepts_a_past_time_from_today():
    scheduler = _scheduler(
        _store=MagicMock(load_keyed=MagicMock(return_value="2026-05-04T09:30:00")),
        _last_run={},
    )

    scheduler._restore_last_run("모멘텀", NOW)

    assert scheduler._last_run["모멘텀"] == datetime(2026, 5, 4, 9, 30)


def test_last_run_restore_ignores_a_snapshot_from_another_day():
    scheduler = _scheduler(
        _store=MagicMock(load_keyed=MagicMock(return_value="2026-05-01T09:30:00")),
        _last_run={},
    )

    scheduler._restore_last_run("모멘텀", NOW)

    assert scheduler._last_run == {}
