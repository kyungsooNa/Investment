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
    _extract_strategy_name,
    _fmt_date,
    _build_metric_str,
)


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

    assert "ScannedStrategy</b> — 17종목 스캔 (시그널 없음)" in report
    assert "💤 <i>활동 없음: DormantA, DormantB, DormantC 외 1개" in report
    assert "활동 없음: ScannedStrategy" not in report
    assert "StaleStrategy" not in report


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
    assert "장 초반 진입 제한으로 스킵" in report


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
