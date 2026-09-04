"""해외 VBO 게이팅 주문 실행 서비스 테스트 (Phase 4).

핵심 안전 계약: `live_enabled=False`(기본)에서는 broker 주문 메서드가 **절대 호출되지
않는다**(구조적 실주문 잠금). live_enabled=True 일 때만 실호출. dry-run 검증 + Phase 5
canary/kill-switch 가 이 플래그를 켜는 유일한 주체다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange, OverseasOrderReport
from common.types import ErrorCode, ResCommonResponse
from services.notification_service import NotificationCategory, NotificationLevel
from services.overseas_order_execution_service import OverseasOrderExecutionService


def _broker(order_resp=None):
    broker = MagicMock()
    report = OverseasOrderReport(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="buy",
        qty=6, limit_price="150.0", broker_order_no="0001234",
    )
    broker.place_overseas_limit_order = AsyncMock(
        return_value=order_resp or ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=report
        )
    )
    return broker


# ── live_enabled=False: 구조적 실주문 잠금 ────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_default_does_not_call_broker():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=False)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    broker.place_overseas_limit_order.assert_not_called()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    report = resp.data
    assert report.side == "buy"
    assert report.qty == 6
    assert report.broker_order_no == ""  # would-be — 브로커 주문번호 없음
    assert report.raw.get("would_be") is True
    assert report.raw.get("signal_source") == OverseasOrderExecutionService.SIGNAL_SOURCE_PAPER


@pytest.mark.asyncio
async def test_exit_default_does_not_call_broker():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=False)
    resp = await svc.place_exit(code="AAPL", qty=6, limit_price=148.0, reason="stop")
    broker.place_overseas_limit_order.assert_not_called()
    assert resp.data.side == "sell"
    assert resp.data.raw.get("exit_reason") == "stop"


@pytest.mark.asyncio
async def test_paper_mode_works_without_broker():
    """live_enabled=False 면 broker=None 이어도 would-be 주문이 가능해야 한다."""
    svc = OverseasOrderExecutionService(None, live_enabled=False)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data.qty == 6


# ── live_enabled=True: 실호출 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_live_calls_broker_with_buy_side():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True,
                                        default_exchange=OverseasExchange.NASD)
    resp = await svc.place_entry(code="aapl", qty=6, limit_price=150.0)
    broker.place_overseas_limit_order.assert_awaited_once()
    kwargs = broker.place_overseas_limit_order.await_args.kwargs
    assert kwargs["side"] == "buy"
    assert kwargs["symbol"] == "AAPL"  # 대문자 정규화
    assert kwargs["qty"] == 6
    assert kwargs["limit_price"] == "150.0"
    assert kwargs["exchange"] == OverseasExchange.NASD
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data.broker_order_no == "0001234"


@pytest.mark.asyncio
async def test_exit_live_calls_broker_with_sell_side():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    await svc.place_exit(code="AAPL", qty=6, limit_price=148.0, reason="eod")
    kwargs = broker.place_overseas_limit_order.await_args.kwargs
    assert kwargs["side"] == "sell"


@pytest.mark.asyncio
async def test_live_propagates_broker_failure():
    broker = _broker(ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="rejected", data=None))
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.API_ERROR.value


# ── 검증: 어떤 모드에서도 잘못된 입력은 broker 도달 전 차단 ────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("qty", [0, -1])
async def test_invalid_qty_rejected_before_broker(qty):
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    resp = await svc.place_entry(code="AAPL", qty=qty, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.place_overseas_limit_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0, -5.0])
async def test_invalid_price_rejected_before_broker(price):
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=price)
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.place_overseas_limit_order.assert_not_called()


# ── kill-switch 게이트 (live 경로) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_live_blocked_by_kill_switch_does_not_call_broker():
    broker = _broker()
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(False, "일일 손실 한도 초과"))
    svc = OverseasOrderExecutionService(broker, live_enabled=True, kill_switch=ks)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    broker.place_overseas_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_live_allowed_by_kill_switch_calls_broker():
    broker = _broker()
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(True, None))
    svc = OverseasOrderExecutionService(broker, live_enabled=True, kill_switch=ks)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    broker.place_overseas_limit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_paper_mode_skips_kill_switch_check():
    """paper(live off)는 실주문이 없으므로 kill-switch 를 호출하지 않는다."""
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(False, "blocked"))
    svc = OverseasOrderExecutionService(None, live_enabled=False, kill_switch=ks)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    ks.check_orders_allowed.assert_not_called()


# ── 일봉 기반 exit 판정 (순수 로직) ───────────────────────────────────────────

def test_decide_daily_exit_stop_when_low_breaks_stop():
    out = OverseasOrderExecutionService.decide_daily_exit(
        entry_price=100.0, stop_price=97.0,
        daily_bar={"low": 96.0, "close": 99.0},
    )
    assert out == {"exit_price": 97.0, "exit_reason": "stop"}


def test_decide_daily_exit_eod_when_stop_not_hit():
    out = OverseasOrderExecutionService.decide_daily_exit(
        entry_price=100.0, stop_price=97.0,
        daily_bar={"low": 98.0, "close": 101.0},
    )
    assert out == {"exit_price": 101.0, "exit_reason": "eod"}


def test_decide_daily_exit_none_on_invalid_bar():
    assert OverseasOrderExecutionService.decide_daily_exit(
        entry_price=100.0, stop_price=97.0, daily_bar={}
    ) is None


# ── 저널 연동 (선택) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_records_order_when_provided():
    journal = MagicMock()
    svc = OverseasOrderExecutionService(None, live_enabled=False, journal=journal)
    await svc.place_entry(code="AAPL", qty=6, limit_price=150.0,
                          signal={"reason": "vbo_daily_breakout"})
    journal.record.assert_called_once()
    kwargs = journal.record.call_args.kwargs
    assert kwargs["code"] == "AAPL"
    assert kwargs["signal_source"] == OverseasOrderExecutionService.SIGNAL_SOURCE_PAPER


@pytest.mark.asyncio
async def test_journal_defaults_to_vbo_strategy_name():
    """기본 저널 전략명은 VBO 자동 경로 이름 — 회귀 잠금."""
    journal = MagicMock()
    svc = OverseasOrderExecutionService(None, live_enabled=False, journal=journal)
    await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert journal.record.call_args.kwargs["strategy_name"] == "LarryWilliamsVBO_overseas"


@pytest.mark.asyncio
async def test_journal_strategy_name_override():
    """수동 주문 경로가 자기 이름으로 기록할 수 있어야 한다(자동 VBO 기록과 혼동 방지)."""
    journal = MagicMock()
    svc = OverseasOrderExecutionService(
        _broker(), live_enabled=True, journal=journal,
        journal_strategy_name="수동매매_해외",
    )
    await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    kwargs = journal.record.call_args.kwargs
    assert kwargs["strategy_name"] == "수동매매_해외"
    assert kwargs["signal_source"] == OverseasOrderExecutionService.SIGNAL_SOURCE_LIVE


# ── USD 거래 원장 연동 (선택) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trade_repository_records_successful_paper_entry():
    trade_repository = MagicMock()
    trade_repository.log_buy_async = AsyncMock()
    svc = OverseasOrderExecutionService(
        None,
        live_enabled=False,
        trade_repository=trade_repository,
        journal_strategy_name="LarryWilliamsVBO_overseas_intraday",
    )

    await svc.place_entry(code="msft", qty=1, limit_price=502.46)

    trade_repository.log_buy_async.assert_awaited_once_with(
        "MSFT",
        OverseasExchange.NASD,
        502.46,
        1,
        source="LarryWilliamsVBO_overseas_intraday",
        order_no="",
    )


@pytest.mark.asyncio
async def test_trade_repository_records_successful_paper_exit():
    trade_repository = MagicMock()
    trade_repository.log_sell_async = AsyncMock()
    svc = OverseasOrderExecutionService(
        None,
        live_enabled=False,
        trade_repository=trade_repository,
        journal_strategy_name="LarryWilliamsVBO_overseas_intraday",
    )

    await svc.place_exit(code="MSFT", qty=1, limit_price=498.0, reason="stop")

    trade_repository.log_sell_async.assert_awaited_once_with(
        "MSFT",
        498.0,
        qty=1,
        reason="stop",
        source="LarryWilliamsVBO_overseas_intraday",
    )


@pytest.mark.asyncio
async def test_trade_repository_skips_rejected_live_order():
    trade_repository = MagicMock()
    trade_repository.log_buy_async = AsyncMock()
    trade_repository.log_sell_async = AsyncMock()
    broker = _broker(ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="rejected", data=None))
    svc = OverseasOrderExecutionService(
        broker,
        live_enabled=True,
        trade_repository=trade_repository,
    )

    await svc.place_entry(code="AAPL", qty=3, limit_price=150.25)

    trade_repository.log_buy_async.assert_not_awaited()
    trade_repository.log_sell_async.assert_not_awaited()


# ── 알림 연동 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_paper_entry_emits_trade_notification():
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    svc = OverseasOrderExecutionService(
        None, live_enabled=False, notification_service=notification_service
    )

    await svc.place_entry(
        code="AAPL",
        qty=3,
        limit_price=150.25,
        signal={"strategy": "LarryWilliamsVBO_overseas_intraday", "reason": "vbo_intraday_breakout"},
    )

    notification_service.emit.assert_awaited_once()
    category, level, title, message = notification_service.emit.await_args.args[:4]
    metadata = notification_service.emit.await_args.kwargs["metadata"]
    assert category == NotificationCategory.STRATEGY
    assert level == NotificationLevel.WARNING
    assert "미국장 VBO BUY" in title
    assert "AAPL" in message
    assert metadata["force_external"] is True
    assert metadata["signal_source"] == OverseasOrderExecutionService.SIGNAL_SOURCE_PAPER
    assert metadata["strategy"] == "LarryWilliamsVBO_overseas_intraday"


@pytest.mark.asyncio
async def test_successful_paper_exit_emits_trade_notification_with_return_rate():
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    svc = OverseasOrderExecutionService(
        None, live_enabled=False, notification_service=notification_service
    )

    await svc.place_exit(
        code="AAPL",
        qty=3,
        limit_price=153.0,
        reason="eod",
        signal={
            "strategy": "LarryWilliamsVBO_overseas_intraday",
            "action": "SELL",
            "realized_pct": 2.0,
        },
    )

    metadata = notification_service.emit.await_args.kwargs["metadata"]
    assert metadata["side"] == "sell"
    assert metadata["exit_reason"] == "eod"
    assert metadata["return_rate"] == 2.0


@pytest.mark.asyncio
async def test_rejected_live_order_does_not_emit_trade_notification():
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    broker = _broker(ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="rejected", data=None))
    svc = OverseasOrderExecutionService(
        broker, live_enabled=True, notification_service=notification_service
    )

    await svc.place_entry(code="AAPL", qty=3, limit_price=150.25)

    notification_service.emit.assert_not_awaited()


# ── 리스크 게이트 / kill switch 연동 (P0-2, P0-3) ────────────────────────

def _live_service(*, risk_gate=None, kill_switch=None, open_positions=0):
    from unittest.mock import AsyncMock as _AM, MagicMock as _MM
    from services.overseas_order_execution_service import OverseasOrderExecutionService
    broker = _MM()
    broker.place_overseas_limit_order = _AM(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None))
    svc = OverseasOrderExecutionService(
        broker=broker, live_enabled=True, kill_switch=kill_switch, risk_gate=risk_gate,
        open_position_count_provider=lambda: open_positions, logger=_MM(),
    )
    return svc, broker


@pytest.mark.asyncio
async def test_risk_gate_blocks_live_entry_before_broker_call():
    from unittest.mock import AsyncMock as _AM, MagicMock as _MM
    gate = _MM()
    gate.validate_order = _AM(return_value=ResCommonResponse(
        rt_cd=ErrorCode.RISK_GATE_BLOCKED.value, msg1="1회 주문 금액 한도 초과", data=None))
    svc, broker = _live_service(risk_gate=gate)

    resp = await svc.place_entry(code="AAA", qty=10, limit_price=100.0, signal={})

    assert resp.rt_cd == ErrorCode.RISK_GATE_BLOCKED.value
    broker.place_overseas_limit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_risk_gate_receives_open_position_count():
    from unittest.mock import AsyncMock as _AM, MagicMock as _MM
    gate = _MM()
    gate.validate_order = _AM(return_value=None)
    svc, _ = _live_service(risk_gate=gate, open_positions=3)

    await svc.place_entry(code="AAA", qty=1, limit_price=100.0, signal={})

    assert gate.validate_order.await_args.kwargs["open_position_count"] == 3


@pytest.mark.asyncio
async def test_paper_mode_never_consults_risk_gate():
    """paper 는 실주문이 없으므로 게이트를 태우지 않는다(관측 신호가 줄면 안 된다)."""
    from unittest.mock import AsyncMock as _AM, MagicMock as _MM
    from services.overseas_order_execution_service import OverseasOrderExecutionService
    gate = _MM()
    gate.validate_order = _AM(return_value=None)
    svc = OverseasOrderExecutionService(
        broker=None, live_enabled=False, risk_gate=gate, logger=_MM())

    resp = await svc.place_entry(code="AAA", qty=1, limit_price=100.0, signal={})

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    gate.validate_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_is_checked_before_risk_gate():
    """kill switch 가 먼저다 — 계좌 보호가 한도 검증보다 우선한다."""
    from unittest.mock import AsyncMock as _AM, MagicMock as _MM
    ks = _MM()
    ks.check_orders_allowed = _AM(return_value=(False, "연속 API 오류"))
    gate = _MM()
    gate.validate_order = _AM(return_value=None)
    svc, broker = _live_service(risk_gate=gate, kill_switch=ks)

    resp = await svc.place_entry(code="AAA", qty=1, limit_price=100.0, signal={})

    assert resp.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    gate.validate_order.assert_not_awaited()
    broker.place_overseas_limit_order.assert_not_awaited()


# ── USD 원장 기록 ────────────────────────────────────────────────────────────
#
# 자동 전략(paper)이 낸 주문은 지금까지 저널·알림에만 남고 원장(`OverseasTradeRepository`)에
# 쓰이지 않아, 미국장 모의투자 화면이 항상 비어 있었다. 원장 기록은 주문 경로의 초크포인트인
# 이 서비스가 담당한다 — VBO(독립 구현)와 베이스 상속 전략 5종이 모두 여기를 지난다.

def _ledger():
    ledger = MagicMock()
    ledger.log_buy_async = AsyncMock(return_value=None)
    ledger.log_sell_async = AsyncMock(return_value=MagicMock(sold_qty=3))
    return ledger


@pytest.mark.asyncio
async def test_paper_entry_is_recorded_in_ledger():
    ledger = _ledger()
    svc = OverseasOrderExecutionService(
        None, live_enabled=False, trade_repository=ledger,
        journal_strategy_name="OverseasIntradayVBO",
    )

    await svc.place_entry(code="aapl", qty=3, limit_price=190.5,
                          exchange=OverseasExchange.NASD, signal={"strategy": "OverseasIntradayVBO"})

    ledger.log_buy_async.assert_awaited_once()
    args, kwargs = ledger.log_buy_async.await_args
    assert args[0] == "AAPL"
    assert args[1] == OverseasExchange.NASD
    assert args[2] == 190.5
    assert args[3] == 3
    assert kwargs["source"] == "OverseasIntradayVBO"


@pytest.mark.asyncio
async def test_paper_exit_is_recorded_in_ledger_with_source_filter():
    """청산은 같은 전략이 남긴 lot 만 닫아야 한다 — 수동 보유를 대신 닫으면 안 된다."""
    ledger = _ledger()
    svc = OverseasOrderExecutionService(None, live_enabled=False, trade_repository=ledger)

    await svc.place_exit(code="AAPL", qty=3, limit_price=200.0, reason="eod",
                         signal={"strategy": "OverseasIntradayVBO"})

    ledger.log_sell_async.assert_awaited_once()
    args, kwargs = ledger.log_sell_async.await_args
    assert args[0] == "AAPL"
    assert args[1] == 200.0
    assert kwargs["qty"] == 3
    assert kwargs["reason"] == "eod"
    assert kwargs["source"] == "OverseasIntradayVBO"


@pytest.mark.asyncio
async def test_ledger_source_falls_back_to_journal_strategy_name():
    """자동 인스턴스는 전략 6종이 공유하므로 주문별 전략명은 signal 에서 온다.

    signal 에 전략명이 없으면 인스턴스 기본값으로 남긴다 — 빈 출처로 쌓이면
    전략별 성과를 나눌 수 없다.
    """
    ledger = _ledger()
    svc = OverseasOrderExecutionService(
        None, live_enabled=False, trade_repository=ledger,
        journal_strategy_name="LarryWilliamsVBO_overseas",
    )

    await svc.place_entry(code="AAPL", qty=1, limit_price=100.0, signal={})

    assert ledger.log_buy_async.await_args.kwargs["source"] == "LarryWilliamsVBO_overseas"


@pytest.mark.asyncio
async def test_rejected_order_is_not_recorded_in_ledger():
    """거부된 주문을 기록하면 유령 보유가 생긴다."""
    ledger = _ledger()
    svc = OverseasOrderExecutionService(None, live_enabled=False, trade_repository=ledger)

    await svc.place_entry(code="AAPL", qty=0, limit_price=100.0, signal={})

    ledger.log_buy_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_ledger_failure_does_not_break_the_order_result():
    """원장 실패가 이미 나간 주문의 결과를 가리면 안 된다."""
    ledger = _ledger()
    ledger.log_buy_async = AsyncMock(side_effect=RuntimeError("db locked"))
    svc = OverseasOrderExecutionService(
        None, live_enabled=False, trade_repository=ledger, logger=MagicMock())

    resp = await svc.place_entry(code="AAPL", qty=1, limit_price=100.0, signal={})

    assert resp.rt_cd == ErrorCode.SUCCESS.value


@pytest.mark.asyncio
async def test_no_ledger_injected_is_noop():
    """원장 미주입 인스턴스(수동 주문 경로)는 그대로 동작한다."""
    svc = OverseasOrderExecutionService(None, live_enabled=False)
    resp = await svc.place_entry(code="AAPL", qty=1, limit_price=100.0, signal={})
    assert resp.rt_cd == ErrorCode.SUCCESS.value


@pytest.mark.asyncio
async def test_live_entry_records_broker_order_no():
    """체결 대사의 매칭 키는 브로커 주문번호다 — live 기록에서 빠지면 대사가 불가능하다."""
    ledger = _ledger()
    broker = _broker()
    svc = OverseasOrderExecutionService(
        broker, live_enabled=True, trade_repository=ledger)

    await svc.place_entry(code="AAPL", qty=6, limit_price=150.0, signal={})

    assert ledger.log_buy_async.await_args.kwargs["order_no"] == "0001234"
