"""scripts/run_overseas_dryrun.py 단위 테스트.

라이브 broker 조립(build_dryrun_service)은 실 KIS 의존이라 통합 영역으로 제외하고,
순수 오케스트레이션(인자 파싱·exchange 해석·scan→flush)만 검증한다.
"""
import subprocess
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

from common.overseas_types import OverseasExchange
from scripts.run_overseas_dryrun import build_parser, resolve_exchange, run_scan


def test_script_help_runs_from_repo_root():
    """문서화된 `python scripts/run_overseas_dryrun.py --help` 실행 경로를 검증한다."""
    result = subprocess.run(
        [sys.executable, "scripts/run_overseas_dryrun.py", "--help"],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    stdout = result.stdout.decode("utf-8", errors="ignore")
    assert "--exchange" in stdout


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.exchange == "NASD"
    assert args.paper is False  # 기본 real(읽기 전용) — 데이터 정합성
    assert args.date is None
    assert args.top_n is None


def test_resolve_exchange_valid():
    assert resolve_exchange("nasd") == OverseasExchange.NASD
    assert resolve_exchange("NYSE") == OverseasExchange.NYSE
    assert resolve_exchange("AMEX") == OverseasExchange.AMEX


def test_resolve_exchange_invalid_raises():
    with pytest.raises(SystemExit):
        resolve_exchange("KRX")


@pytest.mark.asyncio
async def test_run_scan_scans_and_flushes():
    service = MagicMock()
    service.scan_dry_run = AsyncMock(return_value=[{"code": "AAPL", "action": "BUY"}])
    journal = MagicMock()

    signals = await run_scan(
        service, journal, OverseasExchange.NASD, "20260622",
        top_n=50, min_avg_trading_value=None, logger=MagicMock(),
    )

    service.scan_dry_run.assert_awaited_once()
    _, kwargs = service.scan_dry_run.await_args
    assert kwargs["top_n"] == 50
    assert kwargs["record"] is True
    journal.flush_to_file.assert_called_once_with("20260622")
    assert len(signals) == 1


@pytest.mark.asyncio
async def test_run_scan_flushes_even_with_zero_signals():
    service = MagicMock()
    service.scan_dry_run = AsyncMock(return_value=[])
    journal = MagicMock()

    await run_scan(
        service, journal, OverseasExchange.NASD, "20260622",
        top_n=None, min_avg_trading_value=None, logger=MagicMock(),
    )

    journal.flush_to_file.assert_called_once_with("20260622")
