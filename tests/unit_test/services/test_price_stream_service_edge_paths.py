"""PriceStreamService 의 방어 분기·백그라운드 태스크 경로 테스트.

기존 테스트가 정상 틱 소비 흐름을 다루므로, 여기서는 이벤트 루프 없이 호출됐을
때의 fire-and-forget 처리, 부가 recorder/알림 실패 격리, 숫자 파싱 실패 fallback,
상한가 판정처럼 캐시 갱신 자체는 계속되어야 하는 경로를 채운다.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.price_stream_service import PriceStreamService


def _build(**kwargs):
    repo = MagicMock()
    return PriceStreamService(repo, logger=MagicMock(), **kwargs), repo


def _tick(**overrides):
    data = {
        '유가증권단축종목코드': '005930',
        '주식현재가': '75000',
        '누적거래량': '1000',
        '누적거래대금': '75000000',
        '전일대비율': '1.50',
        '전일대비': '1100',
        '전일대비부호': '2',
    }
    data.update(overrides)
    return data


# --- fire-and-forget 태스크 --------------------------------------------------

def test_background_task_without_a_running_loop_closes_the_coroutine():
    """이벤트 루프 밖(동기 테스트/스레드)에서는 코루틴을 닫고 조용히 넘어간다."""
    svc, _ = _build()
    target = AsyncMock()

    svc._schedule_background_task(target, 1)

    assert svc._background_tasks == set()


@pytest.mark.asyncio
async def test_background_task_factory_failure_closes_the_coroutine_and_reraises():
    svc, _ = _build()
    created = []

    async def _coro():
        return None

    def _factory():
        coro = _coro()
        created.append(coro)
        # 태스크 등록 직전에 실패시켜 코루틴이 방치되지 않는지 본다.
        svc._background_tasks = None
        return coro

    with pytest.raises(Exception):
        svc._schedule_background_task(_factory)

    assert created[0].cr_running is False


@pytest.mark.asyncio
async def test_shutdown_without_pending_tasks_returns_immediately():
    svc, _ = _build()

    await svc.shutdown()

    assert svc._background_tasks == set()


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks_that_outlive_the_timeout():
    svc, _ = _build()

    async def _never():
        await asyncio.Event().wait()

    svc._schedule_background_task(_never)
    assert len(svc._background_tasks) == 1
    pending = next(iter(svc._background_tasks))

    await svc.shutdown(timeout=0.01)

    assert pending.cancelled()
    assert svc._background_tasks == set()


# --- 품질 게이트 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_rejected_tick_is_counted_and_notified():
    quality = MagicMock()
    quality.validate_price_tick.return_value = MagicMock(
        ok=False, severity="error", reason="가격 역주행", code="005930",
        latency_sec=0.2, metadata={}, to_dict=lambda: {"reason": "가격 역주행"},
    )
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc, repo = _build(data_quality_service=quality, notification_service=notifications)

    svc.on_price_tick(_tick())

    assert svc.tick_ingest_stats_snapshot()["005930"]["quality_reject"] == 1
    repo.update_realtime_data.assert_not_called()
    assert len(svc._background_tasks) == 1
    await svc.shutdown()
    notifications.emit.assert_awaited_once()


def test_quality_rejection_notification_outside_a_loop_is_swallowed():
    quality = MagicMock()
    quality.validate_price_tick.return_value = MagicMock(
        ok=False, severity="error", reason="지연", code="005930",
        latency_sec=0.2, metadata={}, to_dict=lambda: {},
    )
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc, _ = _build(data_quality_service=quality, notification_service=notifications)

    svc.on_price_tick(_tick())

    assert svc.tick_ingest_stats_snapshot()["005930"]["quality_reject"] == 1


# --- 부가 recorder 실패 격리 --------------------------------------------------

def test_execution_strength_recorder_failure_does_not_stop_cache_update():
    recorder = MagicMock()
    recorder.record_tick.side_effect = RuntimeError("db 없음")
    svc, repo = _build(execution_strength_recorder=recorder)

    svc.on_price_tick(_tick())

    svc._logger.warning.assert_called_once()
    repo.update_realtime_data.assert_called_once()


def test_orderbook_recorder_failure_does_not_stop_cache_update():
    recorder = MagicMock()
    recorder.record_tick.side_effect = RuntimeError("디스크 가득")
    svc, repo = _build(orderbook_recorder=recorder)

    svc.on_price_tick(_tick())

    svc._logger.warning.assert_called_once()
    repo.update_realtime_data.assert_called_once()


@pytest.mark.parametrize("volume, tr_pbmn", [("N/A", "N/A"), ("숫자아님", "숫자아님"),
                                             (None, None)])
def test_unparsable_cumulative_fields_fall_back_to_zero(volume, tr_pbmn):
    svc, _ = _build()

    svc.on_price_tick(_tick(누적거래량=volume, 누적거래대금=tr_pbmn))

    cached = svc._latest_prices["005930"]
    assert cached["acml_vol"] == 0
    assert cached["acml_tr_pbmn"] == 0


@pytest.mark.parametrize("raw", ["N/A", "숫자아님", None, ""])
def test_unparsable_ohlc_fields_become_none(raw):
    svc, _ = _build()

    svc.on_price_tick(_tick(주식최고가=raw, 주식최저가=raw, 주식시가=raw))

    cached = svc._latest_prices["005930"]
    assert cached["high"] is None and cached["low"] is None and cached["open"] is None


# --- 관심종목 알림 / 이벤트 라우터 --------------------------------------------

def test_favorite_alert_scheduling_outside_a_loop_is_swallowed():
    alert = MagicMock()
    alert.handle_price_tick = AsyncMock()
    svc, repo = _build(favorite_price_alert_service=alert)

    svc.on_price_tick(_tick())

    repo.update_realtime_data.assert_called_once()


@pytest.mark.asyncio
async def test_favorite_alert_scheduling_failure_is_logged():
    alert = MagicMock()
    alert.handle_price_tick = MagicMock(side_effect=TypeError("시그니처 불일치"))
    svc, _ = _build(favorite_price_alert_service=alert)

    svc.on_price_tick(_tick())

    assert any("관심종목 가격 알림 평가 실패" in str(c)
               for c in svc._logger.warning.call_args_list)


def test_event_router_dispatch_outside_a_loop_is_swallowed():
    router = MagicMock()
    router.on_price_tick = AsyncMock()
    svc, _ = _build(event_router=router)

    svc.on_price_tick(_tick())

    assert svc.tick_ingest_stats_snapshot()["005930"]["dispatched"] == 1


@pytest.mark.asyncio
async def test_event_router_dispatch_failure_is_logged():
    router = MagicMock()
    router.on_price_tick = MagicMock(side_effect=TypeError("시그니처 불일치"))
    svc, _ = _build(event_router=router)

    svc.on_price_tick(_tick())

    assert any("StrategyEventRouter dispatch 실패" in str(c)
               for c in svc._logger.warning.call_args_list)
    assert svc.tick_ingest_stats_snapshot()["005930"]["dispatched"] == 0


def test_event_router_can_be_injected_after_construction():
    router = MagicMock()
    svc, _ = _build()

    svc.set_event_router(router)

    assert svc._event_router is router


# --- 상한가 판정 -------------------------------------------------------------

@pytest.mark.parametrize(
    "data, expected",
    [
        ({'전일대비부호': '1'}, True),
        ({'실시간가격제한구분': 'U'}, True),
        ({'가격제한구분': '상한가'}, True),
        ({'실시간상한가': 'Y'}, True),
        ({'전일대비부호': '2'}, False),
        ({}, False),
    ],
)
def test_upper_limit_detection_from_a_tick(data, expected):
    svc, _ = _build()

    assert svc._is_upper_limit_tick(data) is expected


@pytest.mark.parametrize(
    "rate, sign, expected",
    [("0", "1", True), ("29.5", "2", True), ("10.0", "2", False),
     ("숫자아님", None, False), (None, None, False)],
)
def test_upper_limit_detection_from_a_rest_snapshot(rate, sign, expected):
    svc, _ = _build()

    assert svc._is_upper_limit_snapshot(rate, sign) is expected


# --- REST 스냅샷 캐시 갱신 ----------------------------------------------------

@pytest.mark.parametrize("code, price", [("", "75000"), ("005930", None)])
def test_rest_snapshot_without_code_or_price_is_ignored(code, price):
    svc, repo = _build()

    svc.cache_price_snapshot(code, price)

    assert svc._latest_prices == {}
    repo.update_realtime_data.assert_not_called()


def test_rest_snapshot_falls_back_to_zero_for_unparsable_volumes():
    svc, _ = _build()

    svc.cache_price_snapshot("005930", "75000", volume="N/A", acml_tr_pbmn="숫자아님",
                              high="N/A", low="숫자아님", open_price=None)

    cached = svc._latest_prices["005930"]
    assert cached["acml_vol"] == 0 and cached["acml_tr_pbmn"] == 0
    assert cached["high"] is None and cached["low"] is None and cached["open"] is None
    assert cached["quality_reason"] == "rest_snapshot"


def test_rest_snapshot_repository_failure_is_logged():
    svc, repo = _build()
    repo.update_realtime_data.side_effect = RuntimeError("저장소 오류")

    svc.cache_price_snapshot("005930", "75000")

    svc._logger.warning.assert_called_once()


def test_rest_snapshot_favorite_alert_outside_a_loop_is_swallowed():
    alert = MagicMock()
    alert.handle_price_tick = AsyncMock()
    svc, _ = _build(favorite_price_alert_service=alert)

    svc.cache_price_snapshot("005930", "75000", rate="1.5", sign="2")

    assert svc._latest_prices["005930"]["price"] == "75000"


@pytest.mark.asyncio
async def test_rest_snapshot_favorite_alert_failure_is_logged():
    alert = MagicMock()
    alert.handle_price_tick = MagicMock(side_effect=TypeError("시그니처 불일치"))
    svc, _ = _build(favorite_price_alert_service=alert)

    svc.cache_price_snapshot("005930", "75000")

    assert any("관심종목 REST 가격 알림 평가 실패" in str(c)
               for c in svc._logger.warning.call_args_list)


def test_rest_snapshot_can_skip_the_favorite_alert():
    alert = MagicMock()
    svc, _ = _build(favorite_price_alert_service=alert)

    svc.cache_price_snapshot("005930", "75000", evaluate_favorite_alert=False)

    alert.handle_price_tick.assert_not_called()


# --- 조회 헬퍼 ---------------------------------------------------------------

def test_liquidity_snapshot_is_none_without_a_cached_tick():
    svc, _ = _build()

    assert svc.get_liquidity_snapshot("005930") is None


def test_liquidity_snapshot_is_none_for_a_legacy_cache_without_volume_fields():
    svc, _ = _build()
    svc._latest_prices["005930"] = {"price": "75000"}

    assert svc.get_liquidity_snapshot("005930") is None


def test_blank_code_is_not_cached_as_a_conclusion_snapshot():
    svc, _ = _build()

    svc.cache_conclusion_snapshot("", 120.0)

    assert svc._latest_conclusions == {}


def test_tick_ingest_stats_report_zero_for_codes_that_never_arrived():
    svc, _ = _build()
    svc.on_price_tick(_tick())

    stats = svc.tick_ingest_stats_snapshot(codes=["005930", "000660"])

    assert stats["005930"]["received"] == 1
    assert stats["000660"] == {"received": 0, "quality_reject": 0,
                               "dispatched": 0, "malformed": 0}


def test_malformed_ticks_are_counted_under_the_unknown_key():
    svc, _ = _build()

    svc.on_price_tick({'주식현재가': '75000'})

    assert svc.tick_ingest_stats_snapshot()["__unknown__"]["malformed"] == 1


# --- SSE 팬아웃 --------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_subscriber_queue_drops_the_tick_instead_of_raising():
    svc, _ = _build()
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"code": "005930"})
    svc._sse_queues[("005930", "KRX")] = [queue]

    svc._fanout_sse_tick("005930", "KRX", {"code": "005930"})

    assert queue.qsize() == 1


def test_sse_tick_payload_falls_back_to_zero_for_unparsable_numbers():
    tick = PriceStreamService._build_sse_tick("005930", {
        '주식현재가': 'N/A', '누적거래량': '숫자아님',
        '주식시가': None, '주식최고가': '', '주식최저가': 'N/A',
    })

    assert tick["price"] == 0.0
    assert tick["volume"] == 0
    assert tick["open"] == tick["high"] == tick["low"] == 0.0
    assert tick["sign"] == "3"
