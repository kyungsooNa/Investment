"""StrategyLogReportService 단위 테스트."""
import json
import os
import tempfile
import pytest

from services.strategy_log_report_service import (
    StrategyLogReportService,
    _extract_strategy_name,
    _fmt_date,
)


# ── 헬퍼 ─────────────────────────────────────────────────────────

def _write_log(path: str, entries: list):
    """JSON Lines 형식의 로그 파일을 작성한다."""
    with open(path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _make_entry(event: str, code: str, name: str, date: str = "2026-04-18",
                reason: str = "", price: int = 0) -> dict:
    return {
        "timestamp": f"{date} 10:00:00,000",
        "level": "INFO" if event == "buy_signal_generated" else "DEBUG",
        "name": f"strategy.TestStrategy",
        "data": {"event": event, "code": code, "name": name,
                 "reason": reason, "price": price},
    }


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
    assert "poor_candle_quality" in report


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
async def test_report_rotated_files(log_dir):
    """SizeTimeRotating으로 생성된 인덱스 파일(_2)도 올바르게 파싱된다."""
    log_path = os.path.join(log_dir, "20260418_093000_TestStrategy.log.json_2")
    _write_log(log_path, [
        _make_entry("buy_signal_generated", "000660", "SK하이닉스", reason="돌파"),
    ])

    svc = StrategyLogReportService(log_dir=log_dir)
    report = await svc.generate_report("20260418")

    assert "SK하이닉스" in report
