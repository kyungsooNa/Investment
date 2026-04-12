"""
NewHighTask 단위 테스트.
daily_prices 스냅샷 → w52_high 기준 신고가 필터링 및 텔레그램 리포트 전송 로직 검증.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.newhigh_task import NewHighTask, _ETF_PREFIXES
from interfaces.schedulable_task import TaskState


# ── 공통 스냅샷 헬퍼 ─────────────────────────────────────────────────────

def _snap(code, name, current_price, w52_high, market_cap=0, change_rate="1.0"):
    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "w52_high": w52_high,
        "market_cap": market_cap,
        "change_rate": change_rate,
    }


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_stock_repo():
    repo = MagicMock()
    repo.get_all_daily_snapshots = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_telegram_reporter():
    reporter = MagicMock()
    reporter.send_newhigh_report = AsyncMock()
    return reporter


@pytest.fixture
def mock_notification_service():
    svc = MagicMock()
    svc.emit = AsyncMock()
    return svc


@pytest.fixture
def task(mock_stock_repo, mock_telegram_reporter, mock_notification_service):
    return NewHighTask(
        stock_repo=mock_stock_repo,
        market_calendar_service=None,
        market_clock=None,
        logger=MagicMock(),
        telegram_reporter=mock_telegram_reporter,
        notification_service=mock_notification_service,
    )


@pytest.fixture(autouse=True)
def disable_asyncio_sleep():
    """모든 테스트에서 asyncio.sleep 실제 대기 제거 (Hang 방지)."""
    with patch("asyncio.sleep", new_callable=AsyncMock):
        yield


# ── task_name / _scheduler_label ────────────────────────────────────────

def test_task_name(task):
    assert task.task_name == "newhigh"


def test_scheduler_label(task):
    assert task._scheduler_label == "NewHighTask"


# ── start() ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_sets_running_state(task):
    with patch("asyncio.create_task") as mock_ct:
        await task.start()
    assert task.state == TaskState.RUNNING
    mock_ct.assert_called_once()


@pytest.mark.asyncio
async def test_start_idempotent(task):
    with patch("asyncio.create_task") as mock_ct:
        await task.start()
        await task.start()
    mock_ct.assert_called_once()


# ── _filter_newhigh ──────────────────────────────────────────────────────

def test_filter_newhigh_basic(task):
    snaps = [
        _snap("000001", "종목A", 10000, 10000, market_cap=5_000_000_000),
        _snap("000002", "종목B", 9000, 10000),   # 신고가 미달
        _snap("000003", "종목C", 11000, 10000, market_cap=3_000_000_000),  # 신고가 초과
    ]
    result = task._filter_newhigh(snaps)
    codes = [r["code"] for r in result]
    assert "000002" not in codes
    assert "000001" in codes
    assert "000003" in codes


def test_filter_newhigh_excludes_etf(task):
    snaps = [
        _snap("100001", "KODEX 200", 30000, 30000),
        _snap("100002", "TIGER IT", 5000, 5000),
        _snap("100003", "일반종목", 8000, 8000),
    ]
    result = task._filter_newhigh(snaps)
    names = [r["name"] for r in result]
    assert "KODEX 200" not in names
    assert "TIGER IT" not in names
    assert "일반종목" in names


def test_filter_newhigh_sorted_by_market_cap(task):
    snaps = [
        _snap("A", "소형주", 1000, 1000, market_cap=1_000_000),
        _snap("B", "대형주", 5000, 5000, market_cap=100_000_000_000),
        _snap("C", "중형주", 3000, 3000, market_cap=10_000_000_000),
    ]
    result = task._filter_newhigh(snaps)
    assert result[0]["code"] == "B"
    assert result[1]["code"] == "C"
    assert result[2]["code"] == "A"


def test_filter_newhigh_zero_price_excluded(task):
    snaps = [
        _snap("Z1", "무가종목", 0, 0),
        _snap("Z2", "w52_0", 5000, 0),
    ]
    result = task._filter_newhigh(snaps)
    assert result == []


# ── _on_market_closed ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_market_closed_sends_telegram(task, mock_stock_repo, mock_telegram_reporter):
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000),
    ]
    await task._on_market_closed("20260412")
    mock_telegram_reporter.send_newhigh_report.assert_awaited_once()
    args = mock_telegram_reporter.send_newhigh_report.call_args
    stocks, date = args[0]
    assert date == "20260412"
    assert len(stocks) == 1
    assert stocks[0]["code"] == "005930"


@pytest.mark.asyncio
async def test_on_market_closed_no_snapshots_skips_telegram(task, mock_stock_repo, mock_telegram_reporter):
    mock_stock_repo.get_all_daily_snapshots.return_value = []
    await task._on_market_closed("20260412")
    mock_telegram_reporter.send_newhigh_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_market_closed_no_newhigh_still_sends(task, mock_stock_repo, mock_telegram_reporter):
    """신고가 종목이 없어도 빈 리스트로 리포트 전송."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("000001", "종목A", 9000, 10000),  # 신고가 미달
    ]
    await task._on_market_closed("20260412")
    mock_telegram_reporter.send_newhigh_report.assert_awaited_once()
    stocks = mock_telegram_reporter.send_newhigh_report.call_args[0][0]
    assert stocks == []


@pytest.mark.asyncio
async def test_on_market_closed_idempotent(task, mock_stock_repo, mock_telegram_reporter):
    """같은 거래일 두 번 호출 시 두 번째는 건너뜀."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000),
    ]
    await task._on_market_closed("20260412")
    await task._on_market_closed("20260412")
    assert mock_telegram_reporter.send_newhigh_report.await_count == 1


@pytest.mark.asyncio
async def test_on_market_closed_new_date_reprocesses(task, mock_stock_repo, mock_telegram_reporter):
    """날짜가 바뀌면 재처리."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000),
    ]
    await task._on_market_closed("20260411")
    await task._on_market_closed("20260412")
    assert mock_telegram_reporter.send_newhigh_report.await_count == 2


@pytest.mark.asyncio
async def test_on_market_closed_emits_notification(task, mock_stock_repo, mock_notification_service):
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000),
    ]
    await task._on_market_closed("20260412")
    mock_notification_service.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_market_closed_no_telegram_reporter(mock_stock_repo, mock_notification_service):
    """telegram_reporter 없어도 예외 없이 동작."""
    task = NewHighTask(
        stock_repo=mock_stock_repo,
        notification_service=mock_notification_service,
        logger=MagicMock(),
    )
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000),
    ]
    await task._on_market_closed("20260412")  # no exception
    assert task._last_collected_date == "20260412"
