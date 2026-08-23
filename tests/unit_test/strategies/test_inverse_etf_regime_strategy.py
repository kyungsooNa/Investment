# tests/unit_test/strategies/test_inverse_etf_regime_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from common.types import ResCommonResponse, TradeSignal
from strategies.inverse_etf_regime_strategy import InverseEtfRegimeStrategy
from strategies.inverse_etf_regime_types import (
    InverseEtfRegimeConfig,
    InverseEtfPositionState,
)
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.market_regime_service import MarketRegimeService, RegimeSnapshot
from core.market_clock import MarketClock


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _price_resp(current="10000"):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_prpr": current}},
    )


def _ma_resp(value):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[{"code": "114800", "date": "20250101", "close": 10000.0, "ma": value}],
    )


def _regime(label):
    return RegimeSnapshot(
        market="KOSPI",
        trend_status="hard_decline" if label == "bear" else "rising",
        regime_label=label,
        snapshot_date="20250102",
        is_rising=(label != "bear"),
        net_change_pct=-0.5 if label == "bear" else 0.5,
        max_daily_drop_pct=-0.3 if label == "bear" else 0.0,
    )


# ── 공통 Fixture ──────────────────────────────────────────────────

@pytest.fixture
def mock_deps():
    sqs = MagicMock(spec=StockQueryService)
    regime = MagicMock(spec=MarketRegimeService)
    indicator = MagicMock(spec=IndicatorService)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()

    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    regime.classify = AsyncMock(spec=MarketRegimeService.classify)
    indicator.get_moving_average = AsyncMock(spec=IndicatorService.get_moving_average)

    return sqs, regime, indicator, tm, logger


@pytest.fixture
def strategy(mock_deps):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(sqs, regime, indicator, tm, logger=logger)
    strat._position_state = {}
    strat._cooldown = {}
    strat._save_state = MagicMock()
    strat._load_state = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 15, 15, 0)
    return strat


@pytest.fixture
def bear_setup(strategy, mock_deps):
    """모든 진입 조건(베어 레짐 + 추세 확인 + 유효 현재가)이 통과하는 셋업."""
    sqs, regime, indicator, tm, logger = mock_deps
    regime.classify.return_value = _regime("bear")
    indicator.get_moving_average.return_value = _ma_resp(9500.0)  # current(10000) > MA
    sqs.get_current_price.return_value = _price_resp("10000")
    return strategy, sqs, regime, indicator, tm, logger


# ── scan() 진입 테스트 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_emits_buy_when_bear_and_trend_confirmed(bear_setup):
    """베어 레짐 + 인버스 ETF 현재가 > MA → BUY 시그널 1건."""
    strat, _, _, _, _, _ = bear_setup
    signals = await strat.scan()
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, TradeSignal)
    assert sig.action == "BUY"
    assert sig.code == "114800"
    assert sig.strategy_name == strat.name
    assert sig.entry_reason == "inverse_etf_bear_regime"


@pytest.mark.asyncio
async def test_scan_sizes_entry_by_configured_portfolio_fraction(bear_setup):
    """인버스 슬리브는 고정 1주가 아니라 기본 3% 예산으로 진입한다."""
    strat, _, _, _, _, _ = bear_setup

    sig = (await strat.scan())[0]

    # 기본값: 1,000만원 × 3% ÷ 10,000원 = 30주
    assert sig.qty == 30


@pytest.mark.asyncio
async def test_scan_no_signal_when_not_bear(bear_setup):
    """레짐이 베어가 아니면(상승/횡보) 진입하지 않는다 — R-2 디코릴레이션 핵심."""
    strat, _, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bull")
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_signal_when_sideways(bear_setup):
    """횡보장에서도 진입하지 않는다(휩쏘 방지)."""
    strat, _, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("sideways")
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_signal_when_below_ma(bear_setup):
    """베어라도 인버스 ETF가 추세 미확인(현재가 <= MA)이면 진입하지 않는다."""
    strat, sqs, _, indicator, _, _ = bear_setup
    indicator.get_moving_average.return_value = _ma_resp(10500.0)  # current(10000) < MA
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_signal_when_ma_unavailable(bear_setup):
    """MA 조회 실패 시 진입하지 않는다(보수)."""
    strat, _, _, indicator, _, _ = bear_setup
    indicator.get_moving_average.return_value = ResCommonResponse(rt_cd="1", msg1="err", data=None)
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_already_holding(bear_setup):
    """이미 포지션 보유 중이면 중복 진입하지 않는다."""
    strat, _, _, _, _, _ = bear_setup
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=9000, entry_date="20250101", peak_price=9000)
    }
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_buy_signal_has_stop_and_trailing_fields(bear_setup):
    """BUY 시그널은 손절선·트레일링 규칙 등 P3-4 9필드를 채운다."""
    strat, _, _, _, _, _ = bear_setup
    sig = (await strat.scan())[0]
    assert sig.stop_loss_price is not None
    assert sig.stop_loss_price < sig.price
    assert sig.trailing_rule is not None
    assert sig.required_data


# ── check_exits() 청산 테스트 ──────────────────────────────────────

def _hold(code="114800", buy_price=10000, qty=3):
    return {"code": code, "name": "KODEX 인버스", "buy_price": buy_price, "qty": qty}


@pytest.mark.asyncio
async def test_exit_when_regime_flips_to_bull(bear_setup):
    """레짐이 베어에서 이탈하면 즉시 청산(헤지 목적 종료)."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bull")
    sqs.get_current_price.return_value = _price_resp("10000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=10000)
    }
    signals = await strat.check_exits([_hold()])
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "레짐" in signals[0].reason


@pytest.mark.asyncio
async def test_exit_on_hard_stop(bear_setup):
    """베어 유지 중이라도 진입가 대비 하드 스탑 도달 시 손절."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    sqs.get_current_price.return_value = _price_resp("9000")  # -10% < -5%
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=10000)
    }
    signals = await strat.check_exits([_hold(buy_price=10000)])
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "스탑" in signals[0].reason or "손절" in signals[0].reason


@pytest.mark.asyncio
async def test_exit_on_trailing_stop_from_peak(bear_setup):
    """고점 대비 트레일링 스톱(-8%) 도달 시 청산."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    # 고점 12000 기록 후 현재 11000 (고점 대비 -8.3% < -8%)
    sqs.get_current_price.return_value = _price_resp("11000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=12000)
    }
    signals = await strat.check_exits([_hold(buy_price=10000)])
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "트레일" in signals[0].reason


@pytest.mark.asyncio
async def test_no_exit_when_bear_and_above_stops(bear_setup):
    """베어 유지 + 스탑/트레일링 미도달이면 보유 지속."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    sqs.get_current_price.return_value = _price_resp("10500")  # +5%, 고점 갱신
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=10500)
    }
    signals = await strat.check_exits([_hold(buy_price=10000)])
    assert signals == []


@pytest.mark.asyncio
async def test_check_exits_updates_peak(bear_setup):
    """현재가가 기존 고점을 갱신하면 position_state.peak_price 가 올라간다."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    sqs.get_current_price.return_value = _price_resp("13000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=11000)
    }
    await strat.check_exits([_hold(buy_price=10000)])
    assert strat._position_state["114800"].peak_price == 13000


@pytest.mark.asyncio
async def test_check_exits_empty_holdings(strategy):
    """보유가 없으면 빈 리스트."""
    signals = await strategy.check_exits([])
    assert signals == []


# ── 식별자 ────────────────────────────────────────────────────────

def test_strategy_identifiers(strategy):
    assert strategy.strategy_id == "inverse_etf_regime"
    assert isinstance(strategy.name, str) and strategy.name


# ── scan() 추가 거절 경로 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_skips_while_cooldown_is_active(bear_setup):
    strat, _, regime, _, _, _ = bear_setup
    strat._cooldown = {strat._cfg.inverse_etf_code: "20250110"}

    assert await strat.scan() == []
    regime.classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_resumes_after_cooldown_expires(bear_setup):
    strat, _, _, _, _, _ = bear_setup
    strat._cooldown = {strat._cfg.inverse_etf_code: "20250101"}

    assert len(await strat.scan()) == 1


@pytest.mark.asyncio
async def test_scan_rejects_invalid_current_price(bear_setup):
    strat, sqs, _, _, _, _ = bear_setup
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="fail", data=None)

    assert await strat.scan() == []
    assert strat._position_state == {}


@pytest.mark.asyncio
async def test_scan_rejects_when_calculated_qty_is_zero(bear_setup):
    strat, _, _, _, _, _ = bear_setup
    strat._cfg = InverseEtfRegimeConfig(
        total_portfolio_krw=0, position_size_pct=0, min_qty=0, use_fixed_qty=False
    )

    assert await strat.scan() == []


# ── _calculate_qty ────────────────────────────────────────────────

def test_calculate_qty_uses_min_qty_for_invalid_price_or_fixed_mode(strategy):
    strategy._cfg = InverseEtfRegimeConfig(min_qty=2, use_fixed_qty=False)
    assert strategy._calculate_qty(0) == 2
    assert strategy._calculate_qty(-100) == 2

    strategy._cfg = InverseEtfRegimeConfig(min_qty=3, use_fixed_qty=True)
    assert strategy._calculate_qty(10000) == 3


def test_calculate_qty_never_falls_below_min_qty(strategy):
    strategy._cfg = InverseEtfRegimeConfig(
        total_portfolio_krw=10_000, position_size_pct=1, min_qty=1, use_fixed_qty=False
    )

    assert strategy._calculate_qty(1_000_000) == 1


# ── 응답 파싱 헬퍼 ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "resp, expected",
    [
        (None, 0),
        (ResCommonResponse(rt_cd="1", msg1="fail", data={"output": {"stck_prpr": "1"}}), 0),
        (ResCommonResponse(rt_cd="0", msg1="OK", data=None), 0),
        (ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {}}), 0),
        (ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "가격"}}), 0),
        (ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12000"}}), 12000),
    ],
)
def test_extract_current_price_handles_unusable_responses(resp, expected):
    assert InverseEtfRegimeStrategy._extract_current_price(resp) == expected


def test_extract_current_price_supports_object_payload():
    resp = ResCommonResponse(rt_cd="0", msg1="OK", data=MagicMock(stck_prpr="9500"))

    assert InverseEtfRegimeStrategy._extract_current_price(resp) == 9500


@pytest.mark.parametrize(
    "resp, expected",
    [
        (RuntimeError("조회 실패"), None),
        (None, None),
        (ResCommonResponse(rt_cd="1", msg1="fail", data=[{"ma": 1.0}]), None),
        (ResCommonResponse(rt_cd="0", msg1="OK", data=[]), None),
        (ResCommonResponse(rt_cd="0", msg1="OK", data=[{"ma": None}]), None),
        (ResCommonResponse(rt_cd="0", msg1="OK", data=[{"ma": "숫자아님"}]), None),
        (ResCommonResponse(rt_cd="0", msg1="OK", data=[{"ma": "9500"}]), 9500.0),
    ],
)
def test_latest_ma_handles_unusable_responses(resp, expected):
    assert InverseEtfRegimeStrategy._latest_ma(resp) == expected


# ── check_exits 추가 경로 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_exits_skips_holdings_without_code(bear_setup):
    strat, sqs, _, _, _, _ = bear_setup

    assert await strat.check_exits([{"name": "코드없음"}]) == []
    sqs.get_current_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_exits_skips_when_current_price_lookup_fails(bear_setup):
    strat, sqs, _, _, _, _ = bear_setup
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="fail", data=None)

    assert await strat.check_exits([_hold()]) == []


@pytest.mark.asyncio
async def test_check_exits_continues_after_single_symbol_error(bear_setup):
    strat, _, _, _, _, logger = bear_setup
    strat._check_single_exit = AsyncMock(side_effect=RuntimeError("평가 실패"))

    assert await strat.check_exits([_hold()]) == []
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_check_exits_seeds_state_from_holding_when_missing(bear_setup):
    strat, sqs, _, _, _, _ = bear_setup
    sqs.get_current_price.return_value = _price_resp("10000")
    strat._position_state = {}

    await strat.check_exits([_hold(buy_price=9000)])

    state = strat._position_state["114800"]
    assert state.entry_price == 9000
    assert state.peak_price == 10000
    strat._save_state.assert_called_once()


@pytest.mark.asyncio
async def test_check_exits_uses_current_price_when_buy_price_missing(bear_setup):
    strat, sqs, _, _, _, _ = bear_setup
    sqs.get_current_price.return_value = _price_resp("10000")
    strat._position_state = {}

    assert await strat.check_exits([{"code": "114800", "buy_price": 0, "qty": 1}]) == []
    assert strat._position_state["114800"].entry_price == 10000


@pytest.mark.asyncio
async def test_hard_stop_exit_starts_a_cooldown(bear_setup):
    strat, sqs, _, _, _, _ = bear_setup
    sqs.get_current_price.return_value = _price_resp("9000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250102", peak_price=10000)
    }

    signals = await strat.check_exits([_hold(buy_price=10000)])

    assert signals[0].action == "SELL"
    assert "114800" in strat._cooldown
    assert "114800" not in strat._position_state


@pytest.mark.asyncio
async def test_regime_release_exit_does_not_start_a_cooldown(bear_setup):
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bull")
    sqs.get_current_price.return_value = _price_resp("10000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250102", peak_price=10000)
    }

    signals = await strat.check_exits([_hold()])

    assert "레짐 해제" in signals[0].reason
    assert strat._cooldown == {}


# ── 상태 저장/복원 ────────────────────────────────────────────────

def test_apply_loaded_state_ignores_non_dict_payload(strategy):
    strategy._apply_loaded_state(["리스트"])

    assert strategy._position_state == {}
    assert strategy._cooldown == {}


def test_apply_loaded_state_restores_positions_and_cooldown(strategy):
    strategy._apply_loaded_state({
        "positions": {
            "114800": {"entry_price": 10000, "entry_date": "20250102", "peak_price": 10500}
        },
        "cooldown": {"114800": "20250110"},
    })

    assert strategy._position_state["114800"].peak_price == 10500
    assert strategy._cooldown == {"114800": "20250110"}


def test_apply_loaded_state_tolerates_missing_sections(strategy):
    strategy._apply_loaded_state({})

    assert strategy._position_state == {}
    assert strategy._cooldown == {}


def test_payload_round_trips_positions_and_cooldown(strategy):
    strategy._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250102", peak_price=10500)
    }
    strategy._cooldown = {"114800": "20250110"}

    payload = strategy._payload()

    assert payload["positions"]["114800"]["peak_price"] == 10500
    assert payload["cooldown"] == {"114800": "20250110"}


@pytest.mark.asyncio
async def test_load_state_async_absorbs_io_errors(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )

    with patch(
        "strategies.inverse_etf_regime_strategy.StrategyStateIO.load",
        AsyncMock(side_effect=RuntimeError("로드 실패")),
    ):
        await strat.load_state()

    logger.error.assert_called()


@pytest.mark.asyncio
async def test_load_state_async_is_a_noop_for_missing_file(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )

    with patch(
        "strategies.inverse_etf_regime_strategy.StrategyStateIO.load",
        AsyncMock(return_value=None),
    ):
        await strat.load_state()

    assert strat._position_state == {}


@pytest.mark.asyncio
async def test_load_state_async_applies_saved_payload(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )

    with patch(
        "strategies.inverse_etf_regime_strategy.StrategyStateIO.load",
        AsyncMock(return_value={
            "positions": {
                "114800": {"entry_price": 1, "entry_date": "20250102", "peak_price": 2}
            },
            "cooldown": {},
        }),
    ):
        await strat.load_state()

    assert strat._position_state["114800"].peak_price == 2


@pytest.mark.asyncio
async def test_save_state_schedules_async_write_inside_event_loop(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )

    with patch(
        "strategies.inverse_etf_regime_strategy.StrategyStateIO.schedule_save"
    ) as schedule_save:
        strat._save_state()

    schedule_save.assert_called_once()


@pytest.mark.asyncio
async def test_save_state_async_absorbs_io_errors(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )

    with patch(
        "strategies.inverse_etf_regime_strategy.StrategyStateIO.save_atomic",
        AsyncMock(side_effect=RuntimeError("저장 실패")),
    ):
        await strat._save_state_async()

    logger.error.assert_called()


@pytest.mark.asyncio
async def test_save_state_async_writes_current_payload(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )
    strat._cooldown = {"114800": "20250110"}

    save_atomic = AsyncMock()
    with patch("strategies.inverse_etf_regime_strategy.StrategyStateIO.save_atomic", save_atomic):
        await strat._save_state_async()

    assert save_atomic.await_args.args[1]["cooldown"] == {"114800": "20250110"}


def test_sync_state_io_reads_and_writes_outside_event_loop(mock_deps, tmp_path):
    """이벤트 루프 밖(부트스트랩 시점)에서는 동기 파일 경로를 쓴다."""
    sqs, regime, indicator, tm, logger = mock_deps
    state_file = tmp_path / "state.json"

    writer = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(state_file)
    )
    writer._cooldown = {"114800": "20250110"}
    writer._save_state()

    reader = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(state_file)
    )

    assert reader._cooldown == {"114800": "20250110"}


def test_sync_load_absorbs_broken_state_file(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    state_file = tmp_path / "state.json"
    state_file.write_text("{not json", encoding="utf-8")

    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(state_file)
    )

    assert strat._position_state == {}
    logger.error.assert_called_once()


def test_sync_save_absorbs_write_failure(mock_deps, tmp_path):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(
        sqs, regime, indicator, tm, logger=logger, state_file=str(tmp_path / "state.json")
    )

    with patch(
        "strategies.inverse_etf_regime_strategy.write_json_atomic",
        side_effect=OSError("디스크 오류"),
    ):
        strat._save_state()

    logger.error.assert_called_once()
