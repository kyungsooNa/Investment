"""StrategyLogReportService 단위 테스트."""
import gzip
import json
import os
import tempfile
import time
import pytest
from types import SimpleNamespace

from services.strategy_log_report_service import (
    StrategyLogReportService,
    _first_number,
    _extract_strategy_name,
    _fmt_date,
    _build_metric_str,
    _is_data_error_reason,
    _ma_proximity_excess_pct,
    _strategy_name_from_source,
    _to_float,
)
from services.strategy_performance_degradation_service import StrategyPerformanceDegradationConfig


# ── 헬퍼 ─────────────────────────────────────────────────────────

def _write_log(path: str, entries: list):
    """JSON Lines 형식의 로그 파일을 작성한다."""
    with open(path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _write_gzip_log(path: str, entries: list, extra_lines: list[bytes] | None = None):
    """gzip JSON Lines 형식의 로그 파일을 작성한다."""
    with gzip.open(path, 'wb') as f:
        for line in extra_lines or []:
            f.write(line)
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False).encode('utf-8') + b'\n')


def _make_entry(event: str, code: str, name: str, date: str = "2026-04-18",
                reason: str = "", price: int = 0) -> dict:
    return {
        "timestamp": f"{date} 10:00:00,000",
        "level": "INFO" if event == "buy_signal_generated" else "DEBUG",
        "name": f"strategy.TestStrategy",
        "data": {"event": event, "code": code, "name": name,
                 "reason": reason, "price": price},
    }


def test_low_level_helpers_cover_empty_and_parse_edges():
    """리포트 헬퍼의 빈 값/파싱 실패/수동 소스명 경계를 검증한다."""
    assert _is_data_error_reason("") is False
    assert _is_data_error_reason("invalid price data: open or current is zero") is True
    assert _to_float(object()) is None
    assert _first_number({"a": "bad", "b": "3.5"}, "a", "b") == 3.5
    assert _first_number({"a": "bad"}, "a") is None
    assert _strategy_name_from_source("") == "미분류"
    assert _strategy_name_from_source("manual:") == "수동매매"
    assert _ma_proximity_excess_pct(None) is None
    assert _ma_proximity_excess_pct(-5.0) == 3.0
    assert _ma_proximity_excess_pct(6.5) == 2.5
    assert _ma_proximity_excess_pct(1.0) == 0.0


def test_metric_string_missing_value_edges():
    """metric 문자열은 필수 숫자가 없으면 빈 문자열을 반환한다."""
    assert _build_metric_str("entry_rejected", "pullback_out_of_range", {}) == ""
    assert _build_metric_str("entry_rejected", "over_extended", {"current": 100}) == ""
    assert _build_metric_str("entry_rejected", "not_near_high", {"distance_pct": 4.0}) == ""
    assert _build_metric_str("entry_rejected", "not_in_uptrend", {"detail": "<bad&detail>"}) == "&lt;bad&amp;detail&gt;"


def test_service_helper_branches_for_summaries_and_quality_labels():
    """섹션 요약/체결품질 헬퍼의 낮은 빈도 분기를 확인한다."""
    svc = StrategyLogReportService(log_dir=".")

    assert svc._format_buy_preview([]) == "• 신규 매수: 없음"
    assert svc._build_rejected_reason_summary({}) is None
    rejected = {
        f"C{i:03d}": {"reason": reason}
        for i, reason in enumerate(
            ["low_execution_strength"] * 5
            + ["insufficient_volume"] * 4
            + ["not_near_high"] * 3
        )
    }
    summary = svc._build_rejected_reason_summary(rejected)
    assert "기타(3건)" in summary

    assert svc._format_order_type_counts({}) == "N/A"
    assert svc._format_order_type_counts({"market": 2, "custom": 1, "limit": 0}) == "시장가 2/custom 1"

    reasons = svc._quality_threshold_reasons(
        avg_slip=0.2,
        p95_slip=0.3,
        avg_latency=1.0,
        incomplete_fill_ratio=2.0,
        avg_unfilled_ratio=8.0,
        avg_order_age=12.0,
        avg_slip_threshold=1.0,
        p95_slip_threshold=1.0,
        avg_latency_threshold=5.0,
        incomplete_fill_ratio_threshold=5.0,
        avg_unfilled_ratio_threshold=5.0,
        avg_order_age_threshold=10.0,
    )
    assert reasons == ["평균 잔량 8.0%", "평균 지속 12.0s"]


def test_execution_quality_record_period_and_label_edges():
    """중복 order_key 최신화와 period/label 경계를 검증한다."""
    cfg = SimpleNamespace(
        enabled=True,
        min_sample_count=2,
        liquidity_control_effective_date="2026-04-02",
        candidate_avg_slippage_pct=1.0,
        candidate_p95_slippage_pct=None,
        candidate_avg_first_fill_latency_sec=None,
        candidate_incomplete_fill_ratio_pct=None,
        candidate_avg_unfilled_ratio_pct=None,
        candidate_avg_order_age_sec=None,
        auto_disable_enabled=True,
        warn_avg_slippage_pct=0.5,
        warn_p95_slippage_pct=None,
        warn_avg_first_fill_latency_sec=None,
        warn_incomplete_fill_ratio_pct=None,
        warn_avg_unfilled_ratio_pct=None,
        warn_avg_order_age_sec=None,
    )
    svc = StrategyLogReportService(log_dir=".", execution_quality_config=cfg)

    latest = svc._latest_execution_quality_records([
        {"order_key": "A", "timestamp": "2026-04-01 09:00:00", "value": 1},
        {"order_key": "A", "timestamp": "2026-04-03 09:00:00", "value": 2},
        {"timestamp": "2026-04-03 09:01:00", "value": 3},
    ])
    assert {item["value"] for item in latest} == {2, 3}
    assert svc._execution_quality_period_label("bad") == ""
    assert svc._execution_quality_period_for_items([
        {"timestamp": "2026-04-01 10:00:00"},
        {"timestamp": "2026-04-03 10:00:00"},
    ]) == "4-2 전후 혼합"

    assert svc._execution_quality_label({"count": 1, "avg_slip": 10.0}) == ""
    candidate = svc._execution_quality_label({"count": 2, "avg_slip": 1.5})
    assert "비활성화 자동 OFF" in candidate

    cfg.candidate_avg_slippage_pct = 2.0
    warn = svc._execution_quality_label({"count": 2, "avg_slip": 0.8})
    assert "경고" in warn


def test_executed_buys_and_portfolio_summary_edges():
    """가상 원장 기반 매수/포트폴리오 요약의 예외와 강제종결 분기를 확인한다."""
    svc = StrategyLogReportService(log_dir=".")
    assert svc._executed_buys_by_strategy("20260418") == (False, {})

    svc._virtual_trade_service = SimpleNamespace(get_all_trades=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert svc._executed_buys_by_strategy("20260418") == (False, {})

    trades = [
        {"buy_date": "2026-04-18 09:00:00", "status": "FAILED", "strategy": "S1", "code": "000001"},
        {"buy_date": "2026-04-18 09:01:00", "status": "HOLD", "strategy": "S1", "code": "005930", "buy_price": "bad"},
    ]
    svc._virtual_trade_service = SimpleNamespace(get_all_trades=lambda: trades, get_holds=lambda: [])
    has_source, by_strategy = svc._executed_buys_by_strategy("20260418")
    assert has_source is True
    assert by_strategy["S1"]["005930"]["price"] == 0

    portfolio_trades = [
        {"buy_date": "2026-04-18 09:00:00", "sell_date": "2026-04-18 10:00:00", "status": "SOLD", "code": "A", "name": "A", "return_rate": 1.0, "sell_price": "bad"},
        {"buy_date": "2026-04-18 09:00:00", "sell_date": "2026-04-18 10:00:00", "status": "SOLD", "code": "B", "name": "B", "return_rate": 0.0, "sell_price": 0, "reason": "reconciled_force_close"},
    ]
    svc._virtual_trade_service = SimpleNamespace(
        get_all_trades=lambda: portfolio_trades,
        get_solds=lambda: portfolio_trades,
        get_holds=lambda: [{"code": "H"}],
    )
    summary = svc._build_portfolio_summary("20260418", {})
    assert "강제 종결" in summary
    assert "현재 보유: 1종목" in summary


def test_portfolio_summary_prefers_net_return_when_available():
    """포트폴리오 요약은 표준 journal의 net_return을 return_rate보다 우선 사용한다."""
    svc = StrategyLogReportService(log_dir=".")
    portfolio_trades = [
        {
            "buy_date": "2026-04-18 09:00:00",
            "sell_date": "2026-04-18 10:00:00",
            "status": "SOLD",
            "code": "A",
            "name": "A",
            "return_rate": 5.0,
            "net_return": 3.25,
            "sell_price": 1100,
        },
    ]
    svc._virtual_trade_service = SimpleNamespace(
        get_all_trades=lambda: portfolio_trades,
        get_solds=lambda: portfolio_trades,
        get_holds=lambda: [],
    )

    summary = svc._build_portfolio_summary("20260418", {})

    assert "평균 순수익률 +3.25%" in summary
    assert "A(A) @ ₩1,100 +3.25%" in summary


def test_backtest_live_divergence_section_from_provider():
    """백테스트 journal provider가 있으면 실거래 원장과 괴리 요약을 생성한다."""
    backtest_records = [{
        "source": "backtest",
        "strategy": "S1",
        "code": "005930",
        "signal_time": "2026-04-18 09:10:00",
        "net_return": 2.5,
        "net_pnl": 2500.0,
        "fill_price": 10500.0,
    }]

    class DummyVirtualTradeService:
        def compare_with_backtest_journal(self, records):
            assert records is backtest_records
            return {
                "summary": {
                    "matched_count": 1,
                    "unmatched_backtest_count": 0,
                    "unmatched_live_count": 0,
                    "avg_net_return_diff": -1.5,
                    "avg_abs_net_return_diff": 1.5,
                    "avg_fill_price_diff_pct": -1.9048,
                    "total_net_pnl_diff": -1500.0,
                },
                "matches": [{
                    "strategy": "S1",
                    "code": "005930",
                    "net_return_diff": -1.5,
                    "fill_price_diff_pct": -1.9048,
                    "net_pnl_diff": -1500.0,
                }],
                "unmatched_backtest": [],
                "unmatched_live": [],
            }

    svc = StrategyLogReportService(
        log_dir=".",
        virtual_trade_service=DummyVirtualTradeService(),
        backtest_journal_provider=lambda target_date: backtest_records if target_date == "20260418" else [],
    )

    section = svc._build_backtest_live_divergence_section("20260418")

    assert "백테스트-실거래 괴리" in section
    assert "매칭: 1건" in section
    assert "평균 순수익률 괴리: -1.50%p" in section
    assert "총 순손익 괴리: -1,500원" in section
    assert "S1/005930: 순수익률 -1.50%p, 체결가 -1.9048%, 순손익 -1,500원" in section


def test_backtest_live_divergence_section_includes_replay_audit_summary():
    backtest_records = [
        {
            "source": "backtest",
            "strategy": "S1",
            "code": "005930",
            "signal_time": "2026-04-18 09:03:00",
            "status": "SIGNAL",
            "metadata": {"audit_status": "missed_by_scheduler"},
        },
        {
            "source": "backtest",
            "strategy": "S1",
            "code": "000660",
            "signal_time": "2026-04-18 09:03:00",
            "status": "REJECTED",
            "rejected_reason": "missing_from_universe",
            "metadata": {"audit_status": "missing_from_universe"},
        },
    ]

    class DummyVirtualTradeService:
        def compare_with_backtest_journal(self, _records):
            return {
                "summary": {
                    "matched_count": 0,
                    "unmatched_backtest_count": 2,
                    "unmatched_live_count": 0,
                },
                "matches": [],
                "unmatched_backtest": backtest_records,
                "unmatched_live": [],
            }

    svc = StrategyLogReportService(
        log_dir=".",
        virtual_trade_service=DummyVirtualTradeService(),
        backtest_journal_provider=lambda _target_date: backtest_records,
    )

    section = svc._build_backtest_live_divergence_section("20260418")

    assert "Replay audit: missed 1건, universe 누락 1건" in section
    assert "S1/005930: missed_by_scheduler" in section


@pytest.mark.asyncio
async def test_report_includes_strategy_degradation_candidates(log_dir):
    """표준 journal 기반 성과 저하 후보를 리포트에 포함하고 getter로 노출한다."""
    fixture_dir = os.path.join("tests", "fixtures", "strategy_degradation")
    with open(os.path.join(fixture_dir, "recent_trades_live.json"), encoding="utf-8") as f:
        live_records = json.load(f)
    with open(os.path.join(fixture_dir, "recent_trades_backtest.json"), encoding="utf-8") as f:
        backtest_records = json.load(f)

    class DummyVirtualTradeService:
        def get_all_trades(self):
            return []

        def get_solds(self):
            return []

        def get_holds(self):
            return []

        def get_standard_journal_records(self):
            return live_records

    _write_log(os.path.join(log_dir, "20260418_093000_S1.log.json"), [
        _make_entry("scan_with_watchlist", "", "", reason=""),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
        backtest_journal_provider=lambda _target_date: backtest_records,
        strategy_degradation_config=StrategyPerformanceDegradationConfig(
            window_size=5,
            min_live_trades=5,
            min_baseline_trades=5,
            capital_base_won=100_000,
            critical_consecutive_losses=3,
        ),
    )

    report = await svc.generate_report("20260418")
    candidates = svc.get_last_strategy_degradation_candidates()

    assert "전략별 성과 저하 후보" in report
    assert "S1: critical_candidate" in report
    assert "연속손실 3" in report
    assert candidates[0]["strategy"] == "S1"


@pytest.mark.asyncio
async def test_strategy_degradation_candidates_include_backtest_live_divergence(log_dir):
    """성과 저하 후보 metadata에 trade-level 백테스트/실거래 괴리 신호를 연결한다."""
    live_records = [{
        "source": "live",
        "strategy": "S1",
        "code": "005930",
        "status": "SOLD",
        "signal_time": "2026-04-18 09:10:00",
        "net_return": -2.0,
        "net_pnl": -2_000.0,
        "fill_price": 10_800.0,
        "metadata": {"sell_date": "2026-04-18 14:50:00"},
    }]
    backtest_records = [{
        "source": "backtest",
        "strategy": "S1",
        "code": "005930",
        "status": "SOLD",
        "signal_time": "2026-04-18 09:10:00",
        "net_return": 2.0,
        "net_pnl": 2_000.0,
        "fill_price": 10_000.0,
        "metadata": {"sell_date": "2026-04-18 14:50:00"},
    }]

    class DummyVirtualTradeService:
        def get_all_trades(self):
            return []

        def get_solds(self):
            return []

        def get_holds(self):
            return []

        def get_standard_journal_records(self):
            return live_records

    _write_log(os.path.join(log_dir, "20260418_093000_S1.log.json"), [
        _make_entry("scan_with_watchlist", "", "", reason=""),
    ])
    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
        backtest_journal_provider=lambda _target_date: backtest_records,
        strategy_degradation_config=StrategyPerformanceDegradationConfig(
            window_size=1,
            min_live_trades=1,
            min_baseline_trades=1,
            warn_avg_return_drop_pctp=1.0,
            warn_win_rate_drop_pctp=100.0,
            warn_profit_factor_below=None,
            critical_consecutive_losses=None,
        ),
    )

    report = await svc.generate_report("20260418")
    candidate = svc.get_last_strategy_degradation_candidates()[0]

    assert "backtest_live_divergence" in candidate["reasons"]
    assert candidate["backtest_live_divergence"]["matched_count"] == 1
    assert candidate["backtest_live_divergence"]["avg_net_return_diff"] == -4.0
    assert candidate["backtest_live_divergence"]["avg_fill_price_diff_pct"] == 8.0
    assert candidate["backtest_live_divergence"]["top_matches"][0]["code"] == "005930"
    assert "백테스트 괴리: 매칭 1건" in report


# ── _extract_strategy_name ────────────────────────────────────────

def test_extract_strategy_name_basic():
    assert _extract_strategy_name("20260418_093000_OneilSqueezeBreakout.log.json") == "OneilSqueezeBreakout"


def test_extract_strategy_name_with_index():
    assert _extract_strategy_name("20260418_093000_FirstPullback.log.json_2") == "FirstPullback"


def test_extract_strategy_name_invalid():
    assert _extract_strategy_name("random_file.txt") is None


def test_fmt_date():
    assert _fmt_date("20260418") == "2026-04-18"
    assert _fmt_date("2026-04-18") == "2026-04-18"


# ── generate_report ───────────────────────────────────────────────

@pytest.fixture
def log_dir():
    """임시 로그 디렉토리를 생성하고 경로를 반환한다."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.mark.asyncio
async def test_report_no_files(log_dir):
    """로그 파일이 없으면 '당일 전략 로그가 없습니다' 메시지를 반환한다."""
    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")
    assert "당일 전략 로그가 없습니다" in report


@pytest.mark.asyncio
async def test_report_buy_signal(log_dir):
    """buy_signal_generated 이벤트가 '매수 완료' 섹션에 포함된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_entry("buy_signal_generated", "005930", "삼성전자",
                    reason="오닐돌파", price=75000),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "삼성전자" in report
    assert "005930" in report
    assert "오닐돌파" in report
    assert "매수 완료" in report


@pytest.mark.asyncio
async def test_report_rejected_event(log_dir):
    """breakout_rejected 이벤트가 '매수 실패' 섹션에 포함된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_entry("breakout_rejected", "036890", "티엘비",
                    reason="low_program_net_buy"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "티엘비" in report
    assert "low_program_net_buy" in report
    assert "매수 실패" in report


@pytest.mark.asyncio
async def test_report_rejected_removed_when_bought(log_dir):
    """탈락 후 매수 신호가 발생하면 해당 종목은 매수 완료로만 분류된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_entry("breakout_rejected", "005930", "삼성전자", reason="low_volume"),
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="오닐돌파"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "매수 완료 (1건)" in report
    assert "매수 실패: 없음" in report


@pytest.mark.asyncio
async def test_report_deduplication_rejected_count(log_dir):
    """동일 종목이 여러 번 탈락한 경우 마지막 사유와 횟수가 표시된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_entry("breakout_rejected", "036890", "티엘비", reason="low_volume"),
        _make_entry("breakout_rejected", "036890", "티엘비", reason="poor_candle_quality"),
        _make_entry("breakout_rejected", "036890", "티엘비", reason="poor_candle_quality"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "3회 탈락" in report
    assert "캔들 위치 미달" in report


@pytest.mark.asyncio
async def test_report_filters_by_date(log_dir):
    """다른 날짜의 이벤트는 리포트에 포함되지 않는다."""
    log_path = os.path.join(log_dir, "20260417_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_entry("buy_signal_generated", "005930", "삼성전자",
                    date="2026-04-17", reason="오닐돌파"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    # 파일이 존재하지만 해당 날짜 이벤트가 없어야 함
    assert "삼성전자" not in report


@pytest.mark.asyncio
async def test_report_multiple_strategies(log_dir):
    """여러 전략의 로그가 각각 섹션으로 분리되어 표시된다."""
    _write_log(os.path.join(log_dir, "20260418_093000_StrategyA.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="A전략 돌파"),
    ])
    _write_log(os.path.join(log_dir, "20260418_093000_StrategyB.log.json"), [
        _make_entry("pp_rejected", "036890", "티엘비", reason="no_ma_proximity"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "StrategyA" in report
    assert "StrategyB" in report
    assert "삼성전자" in report
    assert "티엘비" in report


@pytest.mark.asyncio
async def test_report_highlights_multi_strategy_confluence(log_dir):
    """같은 종목이 2개 이상 전략에서 매수되면 다중 전략 포착 태그가 붙는다."""
    _write_log(os.path.join(log_dir, "20260418_093000_StrategyA.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="A전략 돌파", price=75000),
    ])
    _write_log(os.path.join(log_dir, "20260418_093000_StrategyB.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="B전략 돌파", price=75500),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "[🔥 다중 전략 포착: StrategyA, StrategyB]" in report


@pytest.mark.asyncio
async def test_report_rotated_files(log_dir):
    """SizeTimeRotating으로 생성된 인덱스 파일(_2)도 올바르게 파싱된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json_2")
    _write_log(log_path, [
        _make_entry("buy_signal_generated", "000660", "SK하이닉스", reason="돌파"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "SK하이닉스" in report


@pytest.mark.asyncio
async def test_report_gzip_market_timing_and_db_name_resolution(log_dir):
    """gzip 로그, 시장 타이밍 헤더, DB 종목명 보정이 함께 반영된다."""
    class DummyRepo:
        def get_name_by_code(self, code: str) -> str | None:
            return {"005930": "삼성전자"}.get(code)

    nested_dir = os.path.join(log_dir, "nested")
    os.makedirs(nested_dir, exist_ok=True)
    log_path = os.path.join(nested_dir, "20260418_093000_TestStrategy.log.json.gz")
    _write_gzip_log(
        log_path,
        [
            _make_info_entry("market_timing_updated", "", "", market="KOSPI", ok=True),
            _make_info_entry("market_timing_updated", "", "", market="KOSPI", ok=False),
            _make_info_entry("market_timing_updated", "", "", market="KOSDAQ", ok=False),
            _make_entry("buy_signal_generated", "005930", "005930", reason="오닐돌파", price=75000),
        ],
        extra_lines=[b"\n", b"not-json\n", json.dumps({"timestamp": "2026-04-18 09:00:00,000", "data": []}).encode("utf-8") + b"\n"],
    )

    svc = StrategyLogReportService(log_dir=log_dir, stock_code_repo=DummyRepo())
    report = await svc.generate_report("20260418")

    assert "시장: KOSPI 🔴 | KOSDAQ 🔴" in report
    assert "삼성전자(005930)" in report


@pytest.mark.asyncio
async def test_report_inactive_summary_and_old_file_ignored(log_dir):
    """활동 없는 전략은 요약되고, 48시간보다 오래된 파일은 탐색에서 제외된다."""
    active_path = os.path.join(log_dir, "20260418_093000_ActiveStrategy.log.json")
    _write_log(active_path, [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="돌파"),
    ])

    for name in ["DormantA", "DormantB", "DormantC", "DormantD"]:
        path = os.path.join(log_dir, f"20260418_093000_{name}.log.json")
        _write_log(path, [
            _make_entry("scan_with_watchlist", "", "", date="2026-04-17"),
        ])

    scanned_path = os.path.join(log_dir, "20260418_093000_ScannedStrategy.log.json")
    scanned_entry = _make_entry("scan_with_watchlist", "", "")
    scanned_entry["data"]["count"] = 17
    _write_log(scanned_path, [scanned_entry])

    stale_path = os.path.join(log_dir, "20260418_093000_StaleStrategy.log.json")
    _write_log(stale_path, [
        _make_entry("buy_signal_generated", "000660", "SK하이닉스", reason="오래된로그"),
    ])
    old_mtime = time.time() - (49 * 3600)
    os.utime(stale_path, (old_mtime, old_mtime))

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "ScannedStrategy</b> — 최근 스캔 후보 17종목 (시그널 없음)" in report
    assert "💤 <i>활동 없음: DormantA, DormantB, DormantC 외 1개" in report
    assert "활동 없음: ScannedStrategy" not in report
    assert "StaleStrategy" not in report


@pytest.mark.asyncio
async def test_report_labels_scan_count_as_recent_candidates_and_rejections_as_symbols(log_dir):
    """scan_count 와 탈락 집계가 서로 다른 기준임을 리포트 라벨로 드러낸다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    scan_entry = _make_entry("scan_with_watchlist", "", "")
    scan_entry["data"]["count"] = 2
    entries = [scan_entry]
    entries.extend(
        _make_entry("breakout_rejected", f"A0000{i}", f"종목{i}", reason="poor_candle_quality")
        for i in range(1, 4)
    )
    _write_log(log_path, entries)

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "TestStrategy</b> — 최근 스캔 후보 2종목" in report
    assert "❌ 매수 실패 종목 (3건)" in report
    assert "2종목 스캔" not in report
    assert "❌ 매수 실패 (3건)" not in report


@pytest.mark.asyncio
async def test_report_rejected_limit_shows_rest_count(log_dir):
    """매수 실패 종목이 5건을 넘으면 나머지 건수가 요약 표시된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    entries = [
        _make_entry("breakout_rejected", f"A0000{i}", f"종목{i}", reason="poor_candle_quality")
        for i in range(1, 7)
    ]
    _write_log(log_path, entries)

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "…외 1건" in report
    assert "종목6(A00006)" not in report


@pytest.mark.asyncio
async def test_report_translates_freeform_english_reasons(log_dir):
    """영문 자유형 실패 사유는 한글로 통일해서 출력한다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("breakout_rejected", "000660", "SK하이닉스",
                         reason="Not near high", distance_pct=3.3, threshold=3.0),
        _make_info_entry("breakout_rejected", "005930", "삼성전자",
                         reason="Not in uptrend", close=59000, ma20=60000),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "신고가 근접 미달" in report
    assert "3.3% > 3.0%" in report
    assert "이동평균선 역배열/하락" in report
    assert "종가 59,000 <= MA20 60,000" in report
    assert "Not near high" not in report
    assert "Not in uptrend" not in report


@pytest.mark.asyncio
async def test_report_large_rejected_adds_reason_summary(log_dir):
    """매수 실패가 많으면 사유별 요약 통계를 먼저 출력한다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    entries = []
    entries.extend(
        _make_info_entry("breakout_rejected", f"A0000{i}", f"종목{i}",
                         reason="Not near high", distance_pct=3.3, threshold=3.0)
        for i in range(1, 7)
    )
    entries.extend(
        _make_info_entry("breakout_rejected", f"B0000{i}", f"하락종목{i}",
                         reason="Not in uptrend", close=59000, ma20=60000)
        for i in range(1, 4)
    )
    entries.append(
        _make_info_entry("breakout_rejected", "C00001", "기타종목",
                         reason="poor_candle_quality", pos=0.33)
    )
    _write_log(log_path, entries)

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "주요 탈락 사유: 신고가 근접 미달(6건), 이동평균선 역배열/하락(3건), 기타(1건)" in report


@pytest.mark.asyncio
async def test_report_includes_portfolio_summary_from_virtual_trade_service(log_dir):
    """가상매매 서비스가 있으면 오늘의 포트폴리오 요약이 리포트 하단에 추가된다."""
    class DummyRepo:
        def get_name_by_code(self, code: str) -> str | None:
            return {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER"}.get(code)

    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [
                {"code": "005930", "buy_date": "2026-04-18 09:10:00", "status": "HOLD"},
                {"code": "000660", "buy_date": "2026-04-18 10:20:00", "status": "SOLD"},
            ]

        def get_solds(self):
            return [
                {"code": "000660", "sell_date": "2026-04-18 14:40:00", "return_rate": 3.2},
            ]

        def get_holds(self):
            return [
                {"code": "005930"},
                {"code": "035420"},
            ]

    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="오닐돌파", price=75000),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        stock_code_repo=DummyRepo(),
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260418")

    assert "💰 오늘의 포트폴리오 요약" in report
    assert "신규 매수: 2건 (삼성전자 외 1건)" in report
    assert "당일 청산: 1건 (평균 수익률 +3.20%)" in report
    assert "현재 보유: 2종목" in report


@pytest.mark.asyncio
async def test_report_includes_backtest_live_divergence_when_provider_exists(log_dir):
    """provider가 있으면 리포트 하단에 백테스트-실거래 괴리 섹션을 추가한다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [{"code": "005930", "buy_date": "2026-04-18 09:10:00", "status": "SOLD"}]

        def get_solds(self):
            return [{"code": "005930", "sell_date": "2026-04-18 14:40:00", "net_return": 1.0}]

        def get_holds(self):
            return []

        def compare_with_backtest_journal(self, _records):
            return {
                "summary": {
                    "matched_count": 1,
                    "unmatched_backtest_count": 0,
                    "unmatched_live_count": 0,
                    "avg_net_return_diff": -1.0,
                    "avg_abs_net_return_diff": 1.0,
                    "avg_fill_price_diff_pct": None,
                    "total_net_pnl_diff": -1000.0,
                },
                "matches": [],
                "unmatched_backtest": [],
                "unmatched_live": [],
            }

    _write_log(os.path.join(log_dir, "20260418_093000_TestStrategy.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="돌파", price=75000),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
        backtest_journal_provider=lambda _target_date: [{"code": "005930"}],
    )
    report = await svc.generate_report("20260418")

    assert "백테스트-실거래 괴리" in report
    assert "평균 순수익률 괴리: -1.00%p" in report


@pytest.mark.asyncio
async def test_report_uses_virtual_trade_records_for_completed_buys_when_available(log_dir):
    """운영 원장이 있으면 buy_signal_generated가 아닌 실제 매수 기록 기준으로 매수 완료를 집계한다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [
                {
                    "strategy": "OtherStrategy",
                    "code": "000660",
                    "name": "SK하이닉스",
                    "buy_date": "2026-04-18 09:10:00",
                    "buy_price": 120000,
                    "status": "HOLD",
                },
            ]

        def get_solds(self):
            return []

        def get_holds(self):
            return []

    _write_log(os.path.join(log_dir, "20260418_093000_LarryWilliamsVBO.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="VBO돌파", price=75000),
        _make_entry("breakout_rejected", "035420", "NAVER", reason="low_execution_strength"),
    ])
    _write_log(os.path.join(log_dir, "20260418_093000_OtherStrategy.log.json"), [
        _make_entry("buy_signal_generated", "000660", "SK하이닉스", reason="실행됨", price=120000),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260418")

    vbo_section = report.split("<b>1. LarryWilliamsVBO</b>", 1)[1].split("<b>2. OtherStrategy</b>", 1)[0]
    other_section = report.split("<b>2. OtherStrategy</b>", 1)[1]
    assert "매수 완료: 없음" in vbo_section
    assert "삼성전자" not in vbo_section
    assert "SK하이닉스" in other_section


@pytest.mark.asyncio
async def test_report_does_not_treat_log_buy_as_completed_when_journal_has_untagged_trade(log_dir):
    """원장에 당일 거래가 있으면 전략 태그가 없어도 로그 시그널을 실매수로 보지 않는다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [
                {
                    "code": "000660",
                    "name": "SK하이닉스",
                    "buy_date": "2026-04-18 09:10:00",
                    "buy_price": 120000,
                    "status": "HOLD",
                },
            ]

        def get_solds(self):
            return []

        def get_holds(self):
            return [{"code": "000660"}]

    _write_log(os.path.join(log_dir, "20260418_093000_OneilSqueezeBreakout.log.json"), [
        {**_make_entry("scan_with_watchlist", "", ""), "data": {"event": "scan_with_watchlist", "count": 1}},
        _make_entry("buy_signal_generated", "000001", "종목000001", reason="테스트 로그", price=75000),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260418")

    osb_section = report.split("<b>1. OneilSqueezeBreakout</b>", 1)[1].split("<b>💰 오늘의 포트폴리오 요약</b>", 1)[0]
    assert "시그널 없음" in osb_section
    assert "매수 완료" not in osb_section
    assert "종목000001" not in osb_section
    assert "신규 매수: 1건 (SK하이닉스)" in report


@pytest.mark.asyncio
async def test_report_matches_virtual_trade_strategy_alias_to_log_section(log_dir):
    """원장 전략명이 한글이어도 대응하는 영문 로그 섹션의 매수 완료 detail에 표시한다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [
                {
                    "strategy": "첫눌림목",
                    "code": "005930",
                    "name": "삼성전자",
                    "buy_date": "2026-04-18 09:10:00",
                    "buy_price": 75000,
                    "status": "HOLD",
                    "reason": "원장 체결",
                },
            ]

        def get_solds(self):
            return []

        def get_holds(self):
            return []

    _write_log(os.path.join(log_dir, "20260418_093000_FirstPullback.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="로그 시그널", price=75000),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260418")

    assert "<b>1. FirstPullback</b>" in report
    assert "매수 완료 (1건)" in report
    assert "삼성전자(005930): 원장 체결 @ ₩75,000 (09:10)" in report


@pytest.mark.asyncio
async def test_report_formats_fractional_average_fill_price_with_total_amount(log_dir):
    """원장 평균 매입가가 소수이면 평균체결가/총체결금액으로 표시한다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [
                {
                    "strategy": "RSI2눌림목",
                    "code": "252990",
                    "name": "샘씨엔에스",
                    "buy_date": "2026-06-23 15:10:48",
                    "buy_price": 12931.623376623376,
                    "qty": 154,
                    "status": "HOLD",
                    "reason": "체결 원장 기록",
                },
            ]

        def get_solds(self):
            return []

        def get_holds(self):
            return []

    _write_log(os.path.join(log_dir, "20260623_151000_RSI2Pullback.log.json"), [
        _make_entry("buy_signal_generated", "252990", "샘씨엔에스", date="2026-06-23", reason="RSI2", price=12940),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260623")

    assert "샘씨엔에스(252990): 체결 원장 기록 — 평균체결가 ₩12,931.62 / 총체결금액 ₩1,991,470 (15:10)" in report
    assert "체결 원장 기록 @ ₩12,931" not in report


@pytest.mark.asyncio
async def test_report_matches_virtual_trade_strategy_id_to_vbo_log_section(log_dir):
    """원장 전략명이 stable id여도 대응하는 VBO 로그 섹션의 매수 완료 detail에 표시한다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return [
                {
                    "strategy": "larry_williams_vbo",
                    "code": "403870",
                    "name": "HPSP",
                    "buy_date": "2026-04-18 09:10:00",
                    "buy_price": 54400,
                    "status": "HOLD",
                    "reason": "원장 체결",
                },
            ]

        def get_solds(self):
            return []

        def get_holds(self):
            return []

    _write_log(os.path.join(log_dir, "20260418_093000_LarryWilliamsVBO.log.json"), [
        _make_entry("buy_signal_generated", "403870", "HPSP", reason="로그 시그널", price=54400),
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260418")

    assert "<b>1. LarryWilliamsVBO</b>" in report
    assert "매수 완료 (1건)" in report
    assert "HPSP(403870): 원장 체결 @ ₩54,400 (09:10)" in report


@pytest.mark.asyncio
async def test_report_includes_strategy_regime_decomposition_section(log_dir):
    """일일 리포트 본문에 전략별 regime 분해 섹션을 포함한다."""
    class DummyVirtualTradeService:
        def get_all_trades(self):
            return []

        def get_solds(self):
            return []

        def get_holds(self):
            return []

        def get_standard_journal_records(self):
            return [
                {
                    "strategy": "S1",
                    "code": "000001",
                    "status": "SOLD",
                    "signal_time": "2026-04-15 10:00:00",
                    "net_pnl": 100.0,
                    "net_return": 2.0,
                    "market_regime": {
                        "kospi": "bull",
                        "kosdaq": "bull",
                        "stock_market": "KOSPI",
                    },
                },
                {
                    "strategy": "S2",
                    "code": "000002",
                    "status": "SOLD",
                    "signal_time": "2026-04-16 10:00:00",
                    "net_pnl": 120.0,
                    "net_return": 1.5,
                    "market_regime": {
                        "kospi": "bull",
                        "kosdaq": "bull",
                        "stock_market": "KOSPI",
                    },
                },
            ]

    scan_entry = _make_entry("scan_with_watchlist", "", "", date="2026-04-18")
    scan_entry["data"]["count"] = 2
    _write_log(os.path.join(log_dir, "20260418_093000_FirstPullback.log.json"), [scan_entry])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        virtual_trade_service=DummyVirtualTradeService(),
    )
    report = await svc.generate_report("20260418")

    assert "전략별 시장국면(regime) 분해" in report
    assert "집중도: 2/2" in report
    assert "S1" in report
    assert "S2" in report


@pytest.mark.asyncio
async def test_report_filters_disabled_strategy_sections_and_execution_quality(log_dir):
    """스케줄러에서 제외된 전략은 실행 섹션과 체결 품질 요약에서 제외한다."""
    scan_entry = _make_entry("scan_with_watchlist", "", "", reason="")
    scan_entry["data"]["count"] = 3
    _write_log(os.path.join(log_dir, "20260418_093000_FirstPullback.log.json"), [scan_entry])
    _write_log(os.path.join(log_dir, "20260418_093000_TraditionalVolumeBreakout.log.json"), [
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="전통돌파", price=75000),
    ])
    _write_log(os.path.join(log_dir, "20260418_093000_ExecutionQuality.log.json"), [
        {
            "timestamp": "2026-04-18 10:00:00,000",
            "level": "INFO",
            "name": "strategy.ExecutionQuality",
            "data": {
                "event": "execution_quality",
                "code": "005930",
                "name": "삼성전자",
                "source": "strategy:거래량돌파(전통)",
                "side": "BUY",
                "order_type": "limit",
                "filled_qty": 1,
                "slippage_pct": 2.0,
            },
        },
        {
            "timestamp": "2026-04-18 10:01:00,000",
            "level": "INFO",
            "name": "strategy.ExecutionQuality",
            "data": {
                "event": "execution_quality",
                "code": "000660",
                "name": "SK하이닉스",
                "source": "strategy:첫눌림목",
                "side": "BUY",
                "order_type": "limit",
                "filled_qty": 1,
                "slippage_pct": 0.2,
            },
        },
    ])

    svc = StrategyLogReportService(
        log_dir=log_dir,
        enabled_strategy_provider=lambda: ["첫눌림목"],
    )
    report = await svc.generate_report("20260418")

    assert "FirstPullback" in report
    assert "TraditionalVolumeBreakout" not in report
    assert "거래량돌파(전통)" not in report
    assert "첫눌림목: 1건" in report


@pytest.mark.asyncio
async def test_report_includes_execution_quality_summary(log_dir):
    """execution_quality 로그를 전략별/종목별 체결 품질로 집계한다."""
    log_path = os.path.join(log_dir, "20260418_093000_Execution.log.json")
    _write_log(log_path, [
        {
            "timestamp": "2026-04-18 10:00:00,000",
            "level": "INFO",
            "name": "order.execution",
            "data": {
                "event": "execution_quality",
                "code": "005930",
                "name": "삼성전자",
                "source": "strategy:전략A",
                "side": "BUY",
                "order_type": "limit",
                "spread_pct": 0.2,
                "filled_qty": 10,
                "slippage_pct": 0.2,
                "slippage_amount_won": 140,
                "first_fill_latency_sec": 4.0,
            },
        },
        {
            "timestamp": "2026-04-18 10:01:00,000",
            "level": "INFO",
            "name": "order.execution",
            "data": {
                "event": "execution_quality",
                "code": "005930",
                "name": "삼성전자",
                "source": "strategy:전략A",
                "side": "BUY",
                "order_type": "market",
                "spread_pct": 0.4,
                "filled_qty": 5,
                "slippage_pct": -0.1,
                "slippage_amount_won": -70,
                "first_fill_latency_sec": 2.0,
            },
        },
        {
            "timestamp": "2026-04-18 10:02:00,000",
            "level": "INFO",
            "name": "order.execution",
            "data": {
                "event": "execution_quality",
                "code": "000660",
                "name": "SK하이닉스",
                "source": "manual:수동매매",
                "side": "SELL",
                "order_type": "market",
                "spread_pct": 0.7,
                "filled_qty": 1,
                "slippage_pct": 0.5,
                "first_fill_latency_sec": 9.0,
            },
        },
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "체결 품질 요약" in report
    assert (
        "전략A: 2건, 평균 슬리피지 0.150%, P95 0.195%, 최대 0.200%, 평균 지연 3.0s, "
        "불완전 체결 0.0%, 평균 잔량 N/A, 평균 지속 N/A, 평균 스프레드 0.300%, 주문유형 시장가 1/지정가 1"
    ) in report
    assert (
        "수동매매: 1건, 평균 슬리피지 0.500%, P95 0.500%, 최대 0.500%, 평균 지연 9.0s, "
        "불완전 체결 0.0%, 평균 잔량 N/A, 평균 지속 N/A, 평균 스프레드 0.700%, 주문유형 시장가 1"
    ) in report
    assert "종목별 슬리피지 상위: SK하이닉스(000660) 0.500%/1건, 삼성전자(005930) 0.150%/2건" in report


@pytest.mark.asyncio
async def test_report_marks_poor_execution_quality_strategy(log_dir):
    """설정 기준을 넘는 전략을 경고/비활성화 후보로 표시한다."""
    log_path = os.path.join(log_dir, "20260418_093000_Execution.log.json")
    entries = []
    for idx, slip in enumerate([1.2, 1.4, 2.5], start=1):
        entries.append({
            "timestamp": f"2026-04-18 10:0{idx}:00,000",
            "level": "INFO",
            "name": "order.execution",
            "data": {
                "event": "execution_quality",
                "code": f"00000{idx}",
                "name": f"종목{idx}",
                "source": "strategy:추격전략",
                "side": "BUY",
                "filled_qty": 1,
                "slippage_pct": slip,
                "first_fill_latency_sec": 95.0,
            },
        })
    _write_log(log_path, entries)

    cfg = SimpleNamespace(
        enabled=True,
        min_sample_count=3,
        warn_avg_slippage_pct=0.5,
        warn_p95_slippage_pct=1.0,
        warn_avg_first_fill_latency_sec=30.0,
        candidate_avg_slippage_pct=1.0,
        candidate_p95_slippage_pct=2.0,
        candidate_avg_first_fill_latency_sec=90.0,
        auto_disable_enabled=False,
    )
    svc = StrategyLogReportService(log_dir=log_dir, execution_quality_config=cfg)
    report = await svc.generate_report("20260418")

    assert "추격전략: 3건" in report
    assert "비활성화 후보 1개" in report
    assert "비활성화 후보" in report
    assert "평균 슬리피지" in report
    assert "P95 슬리피지" in report
    assert "평균 지연" in report


@pytest.mark.asyncio
async def test_report_marks_incomplete_fill_quality_strategy(log_dir):
    """부분체결/미체결 지속 시간이 기준을 넘는 전략을 표시한다."""
    log_path = os.path.join(log_dir, "20260418_093000_Execution.log.json")
    _write_log(log_path, [
        {
            "timestamp": f"2026-04-18 10:0{idx}:00,000",
            "level": "INFO",
            "name": "order.execution",
            "data": {
                "event": "execution_quality",
                "order_key": f"order-{idx}",
                "code": f"00000{idx}",
                "name": f"종목{idx}",
                "source": "strategy:잔량전략",
                "side": "BUY",
                "state": "CANCELED" if idx == 1 else "PARTIAL_FILLED",
                "order_qty": 10,
                "filled_qty": 0 if idx == 1 else 4,
                "remaining_qty": 10 if idx == 1 else 6,
                "fill_ratio_pct": 0.0 if idx == 1 else 40.0,
                "unfilled_ratio_pct": 100.0 if idx == 1 else 60.0,
                "order_age_sec": 180.0,
            },
        }
        for idx in range(1, 4)
    ])

    cfg = SimpleNamespace(
        enabled=True,
        min_sample_count=3,
        liquidity_control_effective_date="20260418",
        warn_avg_slippage_pct=10.0,
        warn_p95_slippage_pct=10.0,
        warn_avg_first_fill_latency_sec=999.0,
        warn_incomplete_fill_ratio_pct=20.0,
        warn_avg_unfilled_ratio_pct=20.0,
        warn_avg_order_age_sec=120.0,
        candidate_avg_slippage_pct=20.0,
        candidate_p95_slippage_pct=20.0,
        candidate_avg_first_fill_latency_sec=999.0,
        candidate_incomplete_fill_ratio_pct=80.0,
        candidate_avg_unfilled_ratio_pct=90.0,
        candidate_avg_order_age_sec=300.0,
        auto_disable_enabled=False,
    )
    svc = StrategyLogReportService(log_dir=log_dir, execution_quality_config=cfg)
    report = await svc.generate_report("20260418")

    candidates = svc.get_last_execution_quality_candidates()
    assert candidates
    assert candidates[0]["strategy"] == "잔량전략"
    assert candidates[0]["period"] == "4-2 적용 후"
    assert "잔량전략: 3건" in report
    assert "[4-2 적용 후] 잔량전략" in report
    assert "비활성화 후보 1개" in report
    assert "불완전 체결 100.0%" in report
    assert "평균 잔량 73.3%" in report
    assert "평균 지속 180.0s" in report
    assert "비활성화 후보" in report


# ── _build_metric_str ─────────────────────────────────────────────

def test_build_metric_str_low_execution_strength():
    data = {"cgld": 94.5, "threshold": 100.0}
    assert "94.5%" in _build_metric_str("breakout_rejected", "low_execution_strength", data)
    assert "100%" in _build_metric_str("breakout_rejected", "low_execution_strength", data)


def test_build_metric_str_htf_pattern_detected():
    data = {"surge_ratio": 2.1, "flag_days": 8}
    result = _build_metric_str("htf_pattern_detected", "", data)
    assert "2.1x" in result
    assert "8일" in result


def test_build_metric_str_no_ma_proximity():
    data = {"closest_ma_pct": -1.5}
    assert "-1.50%" in _build_metric_str("pp_rejected", "no_ma_proximity", data)


def test_build_metric_str_projected_volume():
    data = {"proj_vol": 1250000, "threshold": 2000000}
    result = _build_metric_str("breakout_rejected", "insufficient_projected_volume", data)
    assert "1,250,000" in result
    assert "2,000,000" in result


def test_build_metric_str_pullback_out_of_range():
    data = {"pullback_pct": -7.5, "allowed_range": "-3%~-5%"}
    result = _build_metric_str("fp_rejected", "pullback_out_of_range", data)
    assert "-7.5%" in result
    assert "-3%~-5%" in result


def test_build_metric_str_over_extended():
    data = {"current": 112000, "max_entry": 100000}
    result = _build_metric_str("entry_rejected", "over_extended", data)
    assert "초과 +12.0%" == result


# ── near-miss 섹션 ────────────────────────────────────────────────

def _make_info_entry(event: str, code: str, name: str, date: str = "2026-04-18", **extra) -> dict:
    """INFO 레벨 near-miss 로그 항목을 생성한다."""
    return {
        "timestamp": f"{date} 10:30:00,000",
        "level": "INFO",
        "name": "strategy.TestStrategy",
        "data": {"event": event, "code": code, "name": name, **extra},
    }


@pytest.mark.asyncio
async def test_near_miss_section_appears(log_dir):
    """breakout_rejected + low_execution_strength 이벤트가 있으면 🎯 섹션이 출력된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("breakout_rejected", "000660", "SK하이닉스",
                         reason="low_execution_strength", cgld=94.5, threshold=100.0),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "🎯 매수 근접" in report
    assert "SK하이닉스" in report
    assert "체결강도 미달" in report
    assert "94.5%" in report


@pytest.mark.asyncio
async def test_near_miss_highest_gate_wins(log_dir):
    """동일 종목에 여러 rejection이 있을 때 gate가 높은(마지막 관문) 사유만 표시된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("breakout_rejected", "005930", "삼성전자",
                         reason="poor_candle_quality", pos=0.62),
        _make_info_entry("breakout_rejected", "005930", "삼성전자",
                         reason="low_execution_strength", cgld=94.5, threshold=100.0),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "체결강도 미달" in report
    assert "캔들 위치 미달" not in report


@pytest.mark.asyncio
async def test_htf_pattern_detected_in_near_miss(log_dir):
    """htf_pattern_detected 이벤트가 있으면 🎯 섹션에 HTF 패턴 감지로 포함된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("htf_pattern_detected", "035420", "NAVER",
                         surge_ratio=2.1, flag_days=8, drawdown_pct=12.5),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "🎯 매수 근접" in report
    assert "NAVER" in report
    assert "HTF 패턴 감지" in report
    assert "2.1x" in report


@pytest.mark.asyncio
async def test_htf_near_miss_shows_early_morning_guard_note(log_dir):
    """HTF 패턴 감지 후 장 초반 가드로 스킵되면 근접 사유에 방어 로직을 표시한다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("htf_pattern_detected", "035420", "NAVER",
                         surge_ratio=2.1, flag_days=8),
        _make_info_entry("breakout_skipped", "035420", "NAVER",
                         reason="early_morning_guard"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "HTF 패턴 감지" in report
    assert "장 초반 진입 제한, 이후 스캔 계속" in report


@pytest.mark.asyncio
async def test_bought_excluded_from_near_miss(log_dir):
    """매수 완료된 종목은 near-miss 섹션에 표시되지 않는다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("breakout_rejected", "005930", "삼성전자",
                         reason="low_execution_strength", cgld=94.5, threshold=100.0),
        _make_entry("buy_signal_generated", "005930", "삼성전자", reason="OSB돌파", price=85000),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "매수 완료 (1건)" in report
    assert "🎯 매수 근접" not in report


@pytest.mark.asyncio
async def test_near_miss_top3_limit(log_dir):
    """near-miss 후보가 3개 초과여도 상위 3개(gate 높은 순)만 출력된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("breakout_rejected", "A00001", "종목A",
                         reason="smart_money_filter_failed"),          # gate=7
        _make_info_entry("breakout_rejected", "A00002", "종목B",
                         reason="low_execution_strength", cgld=90.0, threshold=100.0),  # gate=6
        _make_info_entry("entry_rejected",    "A00003", "종목C",
                         reason="no_bullish_reversal"),                # gate=5
        _make_info_entry("breakout_rejected", "A00004", "종목D",
                         reason="poor_candle_quality", pos=0.55),      # gate=4
        _make_info_entry("pp_rejected",       "A00005", "종목E",
                         reason="no_ma_proximity", closest_ma_pct=-3.2),  # gate=2
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    # near-miss Top3 reason_kr은 한국어로만 표시됨 (rejected는 영문 reason 사용)
    assert "수급 미달" in report      # 종목A gate=7 → near-miss 포함
    assert "체결강도 미달" in report   # 종목B gate=6 → near-miss 포함
    assert "반등 미확인" in report     # 종목C gate=5 → near-miss 포함
    # near-miss 섹션에만 없으면 됨 (매수 실패 섹션에는 동일 한국어 사유가 표시될 수 있음)
    near_miss_section = report.split("🎯 매수 근접")[1] if "🎯 매수 근접" in report else ""
    assert "종목D" not in near_miss_section  # gate=4 → near-miss 제외
    assert "종목E" not in near_miss_section  # gate=2 → near-miss 제외


@pytest.mark.asyncio
async def test_near_miss_ma_proximity_filters_far_distance_and_sorts_closest(log_dir):
    """MA 거리 초과 near-miss는 허용 범위에 가까운 종목만 가까운 순으로 표시한다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json")
    _write_log(log_path, [
        _make_info_entry("pp_rejected", "A00001", "먼종목",
                         reason="no_ma_proximity", closest_ma_pct=19.61),
        _make_info_entry("pp_rejected", "A00002", "덜가까운종목",
                         reason="no_ma_proximity", closest_ma_pct=7.54),
        _make_info_entry("pp_rejected", "A00003", "가까운종목",
                         reason="no_ma_proximity", closest_ma_pct=4.04),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")
    near_miss_section = report.split("🎯 매수 근접")[1] if "🎯 매수 근접" in report else ""

    assert "가까운종목" in near_miss_section
    assert "덜가까운종목" in near_miss_section
    assert "먼종목" not in near_miss_section
    assert near_miss_section.index("가까운종목") < near_miss_section.index("덜가까운종목")
