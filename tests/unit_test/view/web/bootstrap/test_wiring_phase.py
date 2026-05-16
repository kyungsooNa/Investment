"""WiringPhase 단위 테스트.

ServiceContainer 가 인스턴스 생성만 책임지고, 모든 후주입/상호참조
연결은 WiringPhase 가 수행한다는 contract 를 검증한다. 누락 시
즉시 발견되도록 각 wire 마다 단위 테스트를 둔다.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _make_fake_context():
    ctx = SimpleNamespace()
    ctx.logger = MagicMock()
    ctx.market_data_service = MagicMock()
    ctx.data_quality_service = MagicMock()
    ctx.indicator_service = MagicMock()
    ctx.stock_query_service = MagicMock()
    ctx.favorite_service = MagicMock()
    ctx.stock_repository = MagicMock()
    ctx.rs_rating_service = MagicMock()
    ctx.minervini_stage_service = MagicMock()
    ctx.minervini_update_task = MagicMock()
    ctx.daily_price_collector_task = MagicMock()
    ctx.price_stream_service = MagicMock()
    ctx.price_subscription_service = MagicMock()
    ctx.streaming_service = MagicMock()
    ctx.streaming_stock_repo = MagicMock()
    ctx.program_trading_stream_service = MagicMock()
    ctx.order_execution_service = MagicMock()
    return ctx


def test_wiring_phase_back_injects_data_quality_into_market_data():
    from view.web.bootstrap.wiring_phase import WiringPhase

    ctx = _make_fake_context()
    WiringPhase(ctx).run()

    ctx.market_data_service.set_data_quality_service.assert_called_once_with(ctx.data_quality_service)


def test_wiring_phase_wires_indicator_and_favorite_collaborators():
    from view.web.bootstrap.wiring_phase import WiringPhase

    ctx = _make_fake_context()
    WiringPhase(ctx).run()

    assert ctx.indicator_service.stock_query_service is ctx.stock_query_service
    assert ctx.favorite_service.stock_query_service is ctx.stock_query_service
    assert ctx.favorite_service.stock_repository is ctx.stock_repository
    assert ctx.favorite_service.rs_rating_service is ctx.rs_rating_service
    assert ctx.favorite_service.minervini_stage_service is ctx.minervini_stage_service


def test_wiring_phase_links_minervini_circular_pair():
    from view.web.bootstrap.wiring_phase import WiringPhase

    ctx = _make_fake_context()
    WiringPhase(ctx).run()

    ctx.minervini_stage_service.set_minervini_update_task.assert_called_once_with(ctx.minervini_update_task)
    ctx.minervini_update_task.set_daily_price_collector_task.assert_called_once_with(ctx.daily_price_collector_task)


def test_wiring_phase_skips_minervini_when_either_missing():
    """순환 페어 한쪽이 None 이면 setter 가 호출되지 않는다."""
    from view.web.bootstrap.wiring_phase import WiringPhase

    ctx = _make_fake_context()
    saved_stage = ctx.minervini_stage_service
    ctx.minervini_update_task = None
    WiringPhase(ctx).run()

    saved_stage.set_minervini_update_task.assert_not_called()


def test_wiring_phase_wires_streaming_chain():
    from view.web.bootstrap.wiring_phase import WiringPhase

    ctx = _make_fake_context()
    WiringPhase(ctx).run()

    ctx.data_quality_service.set_price_stream_service.assert_called_once_with(ctx.price_stream_service)
    ctx.streaming_service.set_price_stream_service.assert_called_once_with(ctx.price_stream_service)
    ctx.streaming_service.set_streaming_stock_repo.assert_called_once_with(ctx.streaming_stock_repo)
    ctx.program_trading_stream_service.wire_streaming_stock_repo.assert_called_once_with(ctx.streaming_stock_repo)
    assert ctx.stock_query_service.price_stream_service is ctx.price_stream_service
    assert ctx.stock_query_service.price_subscription_service is ctx.price_subscription_service


def test_wiring_phase_registers_signing_notice_handler():
    from view.web.bootstrap.wiring_phase import WiringPhase

    ctx = _make_fake_context()
    WiringPhase(ctx).run()

    ctx.streaming_service.register_handler.assert_called_once_with(
        "signing_notice",
        ctx.order_execution_service.handle_signing_notice,
    )
