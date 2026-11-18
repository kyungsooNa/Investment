"""해외 모의계좌 체결 프로브(`--fill`) 테스트.

기존 프로브는 현재가의 절반으로 주문해 '접수'만 확인한다. 장중 전략을 모의계좌로
돌리려면 지정가가 실제로 **체결**되는지와 체결내역 응답 형태를 알아야 한다 — 시장성
지정가로 1주 매수 → 체결 확인 → 1주 매도 → 체결 확인, 미체결이면 취소한다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange, OverseasOrderReport
from common.types import ErrorCode, ResCommonResponse
from scripts.probe_overseas_paper_order import fill_probe


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _order(side, odno):
    return _ok(OverseasOrderReport(
        symbol="AAPL", exchange=OverseasExchange.NASD, side=side, qty=1,
        limit_price="1", broker_order_no=odno,
    ))


def _ccnl(*rows):
    return _ok({"output": list(rows)})


def _broker(*, ccnl_responses, order_responses=None, price=200.0):
    broker = MagicMock()
    broker.get_overseas_price = AsyncMock(return_value=_ok(SimpleNamespace(price=price)))
    broker.place_overseas_limit_order = AsyncMock(
        side_effect=order_responses or [_order("buy", "B1"), _order("sell", "S1")])
    broker.inquire_overseas_ccnl = AsyncMock(side_effect=ccnl_responses)
    broker.cancel_overseas_order = AsyncMock(return_value=_ok({}))
    return broker


async def _run(broker, **kwargs):
    lines = []
    code = await fill_probe(
        broker, symbol="AAPL", exchange=OverseasExchange.NASD, trade_date="20261118",
        poll_attempts=kwargs.pop("poll_attempts", 3), sleep=AsyncMock(), out=lines.append,
        **kwargs,
    )
    return code, "\n".join(lines)


@pytest.mark.asyncio
async def test_buy_and_sell_both_fill():
    broker = _broker(ccnl_responses=[
        _ccnl({"odno": "B1", "ft_ccld_qty": "1", "ft_ccld_unpr3": "200.5"}),
        _ccnl({"odno": "S1", "ft_ccld_qty": "1", "ft_ccld_unpr3": "199.8"}),
    ])

    code, out = await _run(broker)

    assert code == 0
    buy, sell = broker.place_overseas_limit_order.call_args_list
    assert buy.kwargs["side"] == "buy" and buy.kwargs["qty"] == 1
    assert buy.kwargs["limit_price"] == "201.00"      # 200 × 1.005 올림
    assert sell.kwargs["side"] == "sell" and sell.kwargs["limit_price"] == "199.00"  # × 0.995 내림
    assert "200.5" in out and "199.8" in out
    broker.cancel_overseas_order.assert_not_called()


@pytest.mark.asyncio
async def test_unfilled_buy_is_cancelled_and_no_sell_is_sent():
    broker = _broker(ccnl_responses=[_ccnl(), _ccnl(), _ccnl()])

    code, _ = await _run(broker)

    assert code == 3
    assert broker.place_overseas_limit_order.await_count == 1
    assert broker.cancel_overseas_order.call_args.kwargs["original_order_no"] == "B1"


@pytest.mark.asyncio
async def test_fill_is_matched_by_order_number_only():
    """다른 주문의 체결 행을 내 체결로 오인하면 안 된다."""
    broker = _broker(ccnl_responses=[
        _ccnl({"odno": "OTHER", "ft_ccld_qty": "1"}), _ccnl(), _ccnl(),
    ])

    code, _ = await _run(broker)

    assert code == 3


@pytest.mark.asyncio
async def test_rejected_buy_reports_error():
    rejected = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="모의 미지원", data=None)
    broker = _broker(ccnl_responses=[], order_responses=[rejected])

    code, out = await _run(broker)

    assert code == 1
    assert "모의 미지원" in out
    broker.inquire_overseas_ccnl.assert_not_called()


@pytest.mark.asyncio
async def test_unfilled_sell_is_cancelled_and_reported():
    broker = _broker(ccnl_responses=[
        _ccnl({"odno": "B1", "ft_ccld_qty": "1", "ft_ccld_unpr3": "200.5"}),
        _ccnl(), _ccnl(), _ccnl(),
    ])

    code, out = await _run(broker)

    assert code == 3
    assert broker.cancel_overseas_order.call_args.kwargs["original_order_no"] == "S1"
    assert "1주 보유" in out


def test_cli_accepts_fill_flag():
    from scripts.probe_overseas_paper_order import build_parser

    args = build_parser().parse_args(["--send", "--fill", "--symbol", "MSFT"])
    assert args.send and args.fill and args.symbol == "MSFT"
