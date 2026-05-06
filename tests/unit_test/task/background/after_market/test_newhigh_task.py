"""
NewHighTask 단위 테스트.
daily_prices 스냅샷 → w52_high 기준 신고가 필터링 및 텔레그램 리포트 전송 로직 검증.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.newhigh_task import NewHighTask, _ETF_PREFIXES
from interfaces.schedulable_task import TaskState


# ── 공통 스냅샷 헬퍼 ─────────────────────────────────────────────────────

def _snap(code, name, current_price, w52_high, market_cap=0, change_rate="1.0", high_price=None, volume=1000, trading_value=200000000):
    if high_price is None:
        high_price = current_price
    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "high_price": high_price,
        "w52_high": w52_high,
        "market_cap": market_cap,
        "change_rate": change_rate,
        "volume": volume,
        "trading_value": trading_value,
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
def mock_stock_query_service():
    sqs = MagicMock()
    sqs.get_ohlcv = AsyncMock()
    return sqs


@pytest.fixture
def task(mock_stock_repo, mock_telegram_reporter, mock_notification_service, mock_stock_query_service):
    return NewHighTask(
        stock_repo=mock_stock_repo,
        market_calendar_service=None,
        market_clock=None,
        logger=MagicMock(),
        telegram_reporter=mock_telegram_reporter,
        notification_service=mock_notification_service,
        stock_query_service=mock_stock_query_service,
    )


@pytest.fixture
def mock_daily_price_collector():
    collector = MagicMock()
    collector.force_run = AsyncMock()
    return collector


@pytest.fixture
def task_with_collector(mock_stock_repo, mock_telegram_reporter, mock_notification_service, mock_daily_price_collector, mock_stock_query_service):
    return NewHighTask(
        stock_repo=mock_stock_repo,
        market_calendar_service=None,
        market_clock=None,
        logger=MagicMock(),
        telegram_reporter=mock_telegram_reporter,
        notification_service=mock_notification_service,
        daily_price_collector_task=mock_daily_price_collector,
        stock_query_service=mock_stock_query_service,
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
        _snap("000004", "종목D", 9000, 10000, high_price=10500), # 유지율 미달 (9000/10500 < 0.97)
        _snap("000005", "종목E", 10000, 10000, volume=0), # 거래량 부족
        _snap("000006", "종목F", 10000, 10000, trading_value=50000), # 거래대금 부족
    ]
    result = task._filter_newhigh(snaps)
    codes = [r["code"] for r in result]
    assert "000002" not in codes
    assert "000004" not in codes
    assert "000005" not in codes
    assert "000006" not in codes
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
async def test_concurrent_run_same_date_executes_once(task, mock_stock_repo, mock_telegram_reporter):
    """같은 거래일 동시 실행 요청은 한 번만 실제 계산한다."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000),
    ]

    await asyncio.gather(
        task._run_newhigh("20260412"),
        task._run_newhigh("20260412"),
    )

    assert mock_stock_repo.get_all_daily_snapshots.await_count == 1
    assert mock_telegram_reporter.send_newhigh_report.await_count == 1


@pytest.mark.asyncio
async def test_telegram_failure_still_marks_completed(task, mock_stock_repo, mock_telegram_reporter):
    """후속 리포트 실패가 신고가 계산 완료 상태를 되돌리지 않는다."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000),
    ]
    mock_telegram_reporter.send_newhigh_report.side_effect = Exception("telegram down")

    await task._on_market_closed("20260412")

    assert task._last_collected_date == "20260412"
    assert task.get_progress()["newhigh_count"] == 1


# ── _has_sufficient_w52_data ────────────────────────────────────────────

def test_has_sufficient_w52_data_all_present(task):
    snaps = [_snap("A", "종목A", 10000, 10000), _snap("B", "종목B", 5000, 5000)]
    assert task._has_sufficient_w52_data(snaps) is True


def test_has_sufficient_w52_data_all_missing(task):
    snaps = [_snap("A", "종목A", 10000, 0), _snap("B", "종목B", 5000, 0)]
    assert task._has_sufficient_w52_data(snaps) is False


def test_has_sufficient_w52_data_none_values(task):
    """w52_high=None 인 경우도 부재로 판단."""
    snaps = [{"code": "A", "name": "종목A", "current_price": 10000, "w52_high": None, "market_cap": 0}]
    assert task._has_sufficient_w52_data(snaps) is False


def test_has_sufficient_w52_data_threshold(task):
    """20% 기준: 5개 중 1개 유효 → 충분 / 0개 → 부족."""
    snaps_one_valid = [
        _snap("A", "A", 10000, 10000),
        _snap("B", "B", 10000, 0),
        _snap("C", "C", 10000, 0),
        _snap("D", "D", 10000, 0),
        _snap("E", "E", 10000, 0),
    ]
    assert task._has_sufficient_w52_data(snaps_one_valid) is True  # 1/5 = 20%

    snaps_none_valid = [_snap(str(i), f"종목{i}", 10000, 0) for i in range(5)]
    assert task._has_sufficient_w52_data(snaps_none_valid) is False


def test_has_sufficient_w52_data_empty(task):
    assert task._has_sufficient_w52_data([]) is True


# ── force_run 트리거 시나리오 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_run_triggered_when_w52_missing(
    task_with_collector, mock_stock_repo, mock_daily_price_collector, mock_telegram_reporter
):
    """w52_high 데이터 부재 시 force_run 호출 후 재조회하여 신고가 탐색."""
    mock_stock_repo.get_all_daily_snapshots.side_effect = [
        # 1차 조회: w52_high 없음 (FDR 수집 케이스)
        [_snap("005930", "삼성전자", 80000, 0, market_cap=500_000_000_000)],
        # 2차 조회 (force_run 후): w52_high 복원
        [_snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000)],
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_run.assert_awaited_once()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 2
    stocks = mock_telegram_reporter.send_newhigh_report.call_args[0][0]
    assert stocks[0]["code"] == "005930"


@pytest.mark.asyncio
async def test_force_run_not_triggered_when_w52_present(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """w52_high 데이터 충분 시 force_run 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000),
        _snap("000660", "SK하이닉스", 50000, 50000, market_cap=300_000_000_000),
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_run.assert_not_awaited()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 1


@pytest.mark.asyncio
async def test_force_run_not_triggered_without_collector(task, mock_stock_repo):
    """daily_price_collector_task 없으면 w52_high 부재여도 force_run 없이 정상 완료."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 0),
    ]
    await task._on_market_closed("20260412")  # no exception
    assert task._last_collected_date == "20260412"


# ── _enrich_historical_high ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_historical_high_true(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(rt_cd="0", data=[{"high": 1000}, {"high": 1500}])
    stocks = [{"code": "005930", "current_price": 2000}]
    
    enriched = await task._enrich_historical_high(stocks)
    
    assert enriched[0]["is_historical_new_high"] is True

@pytest.mark.asyncio
async def test_enrich_historical_high_false(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(rt_cd="0", data=[{"high": 3000}, {"high": 1500}])
    stocks = [{"code": "005930", "current_price": 2000}]
    
    enriched = await task._enrich_historical_high(stocks)
    
    assert enriched[0]["is_historical_new_high"] is False
    
@pytest.mark.asyncio
async def test_enrich_historical_high_api_fail(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(rt_cd="1", data=None)
    stocks = [{"code": "005930", "current_price": 2000}]
    
    enriched = await task._enrich_historical_high(stocks)
    
    assert enriched[0]["is_historical_new_high"] is False


@pytest.mark.asyncio
async def test_force_run_retries_only_once(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """force_run 후 재조회한 데이터에 여전히 w52_high 없어도 두 번째 force_run 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 0),
    ]
    await task_with_collector._on_market_closed("20260412")

    # force_run 는 한 번만
    mock_daily_price_collector.force_run.assert_awaited_once()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 2


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
"""
NewHighTask 단위 테스트.
daily_prices 스냅샷 → w52_high 기준 신고가 필터링 및 텔레그램 리포트 전송 로직 검증.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.newhigh_task import NewHighTask, _ETF_PREFIXES
from interfaces.schedulable_task import TaskState


# ── 공통 스냅샷 헬퍼 ─────────────────────────────────────────────────────

def _snap(code, name, current_price, w52_high, market_cap=0, change_rate="1.0", high_price=None, volume=1000, trading_value=200000000):
    if high_price is None:
        high_price = current_price
    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "high_price": high_price,
        "w52_high": w52_high,
        "market_cap": market_cap,
        "change_rate": change_rate,
        "volume": volume,
        "trading_value": trading_value,
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
def mock_stock_query_service():
    sqs = MagicMock()
    sqs.get_ohlcv = AsyncMock()
    return sqs


@pytest.fixture
def task(mock_stock_repo, mock_telegram_reporter, mock_notification_service, mock_stock_query_service):
    return NewHighTask(
        stock_repo=mock_stock_repo,
        market_calendar_service=None,
        market_clock=None,
        logger=MagicMock(),
        telegram_reporter=mock_telegram_reporter,
        notification_service=mock_notification_service,
        stock_query_service=mock_stock_query_service,
    )


@pytest.fixture
def mock_daily_price_collector():
    collector = MagicMock()
    collector.force_run = AsyncMock()
    return collector


@pytest.fixture
def task_with_collector(mock_stock_repo, mock_telegram_reporter, mock_notification_service, mock_daily_price_collector, mock_stock_query_service):
    return NewHighTask(
        stock_repo=mock_stock_repo,
        market_calendar_service=None,
        market_clock=None,
        logger=MagicMock(),
        telegram_reporter=mock_telegram_reporter,
        notification_service=mock_notification_service,
        daily_price_collector_task=mock_daily_price_collector,
        stock_query_service=mock_stock_query_service,
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
        _snap("000004", "종목D", 9000, 10000, high_price=10500), # 유지율 미달 (9000/10500 < 0.97)
        _snap("000005", "종목E", 10000, 10000, volume=0), # 거래량 부족
        _snap("000006", "종목F", 10000, 10000, trading_value=50000), # 거래대금 부족
    ]
    result = task._filter_newhigh(snaps)
    codes = [r["code"] for r in result]
    assert "000002" not in codes
    assert "000004" not in codes
    assert "000005" not in codes
    assert "000006" not in codes
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


# ── _has_sufficient_w52_data ────────────────────────────────────────────

def test_has_sufficient_w52_data_all_present(task):
    snaps = [_snap("A", "종목A", 10000, 10000), _snap("B", "종목B", 5000, 5000)]
    assert task._has_sufficient_w52_data(snaps) is True


def test_has_sufficient_w52_data_all_missing(task):
    snaps = [_snap("A", "종목A", 10000, 0), _snap("B", "종목B", 5000, 0)]
    assert task._has_sufficient_w52_data(snaps) is False


def test_has_sufficient_w52_data_none_values(task):
    """w52_high=None 인 경우도 부재로 판단."""
    snaps = [{"code": "A", "name": "종목A", "current_price": 10000, "w52_high": None, "market_cap": 0}]
    assert task._has_sufficient_w52_data(snaps) is False


def test_has_sufficient_w52_data_threshold(task):
    """20% 기준: 5개 중 1개 유효 → 충분 / 0개 → 부족."""
    snaps_one_valid = [
        _snap("A", "A", 10000, 10000),
        _snap("B", "B", 10000, 0),
        _snap("C", "C", 10000, 0),
        _snap("D", "D", 10000, 0),
        _snap("E", "E", 10000, 0),
    ]
    assert task._has_sufficient_w52_data(snaps_one_valid) is True  # 1/5 = 20%

    snaps_none_valid = [_snap(str(i), f"종목{i}", 10000, 0) for i in range(5)]
    assert task._has_sufficient_w52_data(snaps_none_valid) is False


def test_has_sufficient_w52_data_empty(task):
    assert task._has_sufficient_w52_data([]) is True


# ── force_run 트리거 시나리오 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_run_triggered_when_w52_missing(
    task_with_collector, mock_stock_repo, mock_daily_price_collector, mock_telegram_reporter
):
    """w52_high 데이터 부재 시 force_run 호출 후 재조회하여 신고가 탐색."""
    mock_stock_repo.get_all_daily_snapshots.side_effect = [
        # 1차 조회: w52_high 없음 (FDR 수집 케이스)
        [_snap("005930", "삼성전자", 80000, 0, market_cap=500_000_000_000)],
        # 2차 조회 (force_run 후): w52_high 복원
        [_snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000)],
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_run.assert_awaited_once()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 2
    stocks = mock_telegram_reporter.send_newhigh_report.call_args[0][0]
    assert stocks[0]["code"] == "005930"


@pytest.mark.asyncio
async def test_force_run_not_triggered_when_w52_present(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """w52_high 데이터 충분 시 force_run 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000),
        _snap("000660", "SK하이닉스", 50000, 50000, market_cap=300_000_000_000),
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_run.assert_not_awaited()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 1


@pytest.mark.asyncio
async def test_force_run_not_triggered_without_collector(task, mock_stock_repo):
    """daily_price_collector_task 없으면 w52_high 부재여도 force_run 없이 정상 완료."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 0),
    ]
    await task._on_market_closed("20260412")  # no exception
    assert task._last_collected_date == "20260412"


# ── _enrich_historical_high ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_historical_high_true(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(rt_cd="0", data=[{"high": 1000}, {"high": 1500}])
    stocks = [{"code": "005930", "current_price": 2000}]
    
    enriched = await task._enrich_historical_high(stocks)
    
    assert enriched[0]["is_historical_new_high"] is True

@pytest.mark.asyncio
async def test_enrich_historical_high_false(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(rt_cd="0", data=[{"high": 3000}, {"high": 1500}])
    stocks = [{"code": "005930", "current_price": 2000}]
    
    enriched = await task._enrich_historical_high(stocks)
    
    assert enriched[0]["is_historical_new_high"] is False
    
@pytest.mark.asyncio
async def test_enrich_historical_high_api_fail(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(rt_cd="1", data=None)
    stocks = [{"code": "005930", "current_price": 2000}]
    
    enriched = await task._enrich_historical_high(stocks)
    
    assert enriched[0]["is_historical_new_high"] is False


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


# ── 추가: 커버리지 보완 TC ──────────────────────────────────────────────

def test_get_progress(task):
    """get_progress()가 복사된 dict를 반환하는지 테스트"""
    task._progress["status"] = "test_status"
    prog = task.get_progress()
    assert prog["status"] == "test_status"
    
    prog["status"] = "changed"
    assert task._progress["status"] == "test_status"


@pytest.mark.asyncio
async def test_force_run_success(task):
    """force_run() 호출 시 mcs에서 날짜를 가져와 _run_newhigh를 호출하는지 검증"""
    task._mcs = MagicMock()
    task._mcs.get_latest_trading_date = AsyncMock(return_value="20260413")
    
    with patch.object(task, "_run_newhigh", new_callable=AsyncMock) as mock_run:
        await task.force_run()
        mock_run.assert_awaited_once_with("20260413")


@pytest.mark.asyncio
async def test_force_run_no_target_date(task):
    """target_date가 없을 경우 force_run()가 중단되는지 검증"""
    task._mcs = MagicMock()
    task._mcs.get_latest_trading_date = AsyncMock(return_value=None)
    
    with patch.object(task, "_run_newhigh", new_callable=AsyncMock) as mock_run:
        await task.force_run()
        mock_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_refresh_deduplicates_background_task(task):
    """백그라운드 갱신 예약이 이미 있으면 중복 create_task 하지 않는다."""
    assert task.trigger_refresh() is True
    assert task.trigger_refresh() is False

    if task._refresh_task:
        await asyncio.gather(task._refresh_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_newhigh_empty_snapshots(task, mock_stock_repo):
    """조회된 snapshot 데이터가 없을 경우 early return 동작 검증"""
    mock_stock_repo.get_all_daily_snapshots.return_value = []
    await task._run_newhigh("20260413")
    # finally 블록을 거쳐 running=False가 되어야 함
    assert task._progress["running"] is False


@pytest.mark.asyncio
async def test_run_newhigh_exception_handled(task, mock_stock_repo):
    """_run_newhigh 내부에서 예외 발생 시 크래시 방지 및 상태 초기화 검증"""
    mock_stock_repo.get_all_daily_snapshots.side_effect = Exception("DB Error")
    await task._run_newhigh("20260413")
    assert task._progress["running"] is False


def test_filter_newhigh_rf_materials(task):
    """RF머트리얼즈 등 특수한 종목명 디버깅 코드 경로 커버"""
    snaps = [
        _snap("123456", "RF머트리얼즈", 10000, 10000, market_cap=5_000_000_000),
    ]
    result = task._filter_newhigh(snaps)
    assert len(result) == 1
    assert result[0]["name"] == "RF머트리얼즈"


@pytest.fixture
def mock_rs_rating_service():
    svc = MagicMock()
    svc.get_ratings_by_date = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_enrich_and_filter_rs_rating_success(task, mock_rs_rating_service):
    """rs_rating 정상 주입 및 임계치 미달 필터링 검증"""
    task._rs_rating_service = mock_rs_rating_service
    task._rs_rating_min = 80
    
    mock_rs_rating_service.get_ratings_by_date.return_value = MagicMock(
        rt_cd="0", data={"005930": 85, "000660": 70}
    )
    
    stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
        {"code": "035420", "name": "NAVER"}, # rating 데이터 없음 -> 0 처리
    ]
    result = await task._enrich_and_filter_rs_rating(stocks, "20260413")
    
    assert len(result) == 1
    assert result[0]["code"] == "005930"
    assert result[0]["rs_rating"] == 85


@pytest.mark.asyncio
async def test_enrich_and_filter_rs_rating_api_fail(task, mock_rs_rating_service):
    """RS Rating 조회 실패 시 rs_rating=0 주입 후 필터 미적용 반환하는지 검증"""
    task._rs_rating_service = mock_rs_rating_service
    mock_rs_rating_service.get_ratings_by_date.return_value = MagicMock(rt_cd="1", data=None)
    stocks = [{"code": "005930", "name": "삼성전자"}]
    result = await task._enrich_and_filter_rs_rating(stocks, "20260413")
    assert len(result) == 1
    assert result[0]["rs_rating"] == 0


@pytest.mark.asyncio
async def test_enrich_and_filter_rs_rating_exception(task, mock_rs_rating_service):
    """RS Rating 조회 중 예외 발생 시에도 앱이 크래시되지 않고 rs_rating=0 주입 검증"""
    task._rs_rating_service = mock_rs_rating_service
    mock_rs_rating_service.get_ratings_by_date.side_effect = Exception("API error")
    stocks = [{"code": "005930", "name": "삼성전자"}]
    result = await task._enrich_and_filter_rs_rating(stocks, "20260413")
    assert len(result) == 1
    assert result[0]["rs_rating"] == 0


@pytest.mark.asyncio
async def test_enrich_and_filter_rs_rating_low_coverage_skips_filter(task, mock_rs_rating_service):
    """RS Rating 부분 데이터가 신고가 후보를 전부 0건으로 만들지 않도록 필터를 건너뛴다."""
    task._rs_rating_service = mock_rs_rating_service
    task._rs_rating_min = 80
    mock_rs_rating_service.get_ratings_by_date.return_value = MagicMock(
        rt_cd="0",
        data={"999999": 99},
    )
    stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
    ]

    result = await task._enrich_and_filter_rs_rating(stocks, "20260413")

    assert len(result) == 2
    assert all(s["rs_rating"] == 0 for s in result)


# ── _filter_newhigh: rs 필드 전달 버그 수정 검증 ─────────────────────────

def test_filter_newhigh_rs_uses_rs_rating_from_snapshot(task):
    """DB 스냅샷의 rs_rating 값이 반환 종목의 rs 필드로 전달되는지 검증."""
    snap = _snap("005930", "삼성전자", 80000, 80000, market_cap=1_000_000_000)
    snap["rs_rating"] = 85
    result = task._filter_newhigh([snap])
    assert len(result) == 1
    assert result[0]["rs"] == 85


def test_filter_newhigh_rs_defaults_to_dash_when_no_rs_rating(task):
    """rs_rating이 없는 스냅샷에서 rs 필드가 '-' 기본값으로 세팅되는지 검증."""
    snap = _snap("005930", "삼성전자", 80000, 80000, market_cap=1_000_000_000)
    # rs_rating 미포함 (DB 컬럼 없는 경우)
    result = task._filter_newhigh([snap])
    assert len(result) == 1
    assert result[0]["rs"] == "-"


def test_filter_newhigh_normalizes_mixed_price_snapshot(task):
    """현재가만 최신값으로 섞인 DB 스냅샷도 신고가 판정과 리포트 등락률을 보정한다."""
    snap = _snap(
        "006800",
        "미래에셋증권",
        84200,
        77600,
        market_cap=415_199,
        change_rate="5.55",
        high_price=77600,
        volume=46_101_232,
        trading_value=149_586_351_500,
    )
    snap.update({
        "open_price": 75500,
        "low_price": 73200,
        "prev_close": 70300,
        "change_price": 3900,
        "change_sign": "2",
    })

    result = task._filter_newhigh([snap])

    assert len(result) == 1
    assert result[0]["high_price"] == 84200
    assert result[0]["change_price"] == 13900
    assert result[0]["change_rate"] == "19.77"
    assert result[0]["is_newhigh"] is True


@pytest.mark.asyncio
async def test_get_newhigh_cache_triggers_refresh_when_empty(task):
    with patch.object(task, "trigger_refresh", return_value=True) as mock_trigger:
        result = await task.get_newhigh_cache()

    assert result == []
    mock_trigger.assert_called_once()


@pytest.mark.asyncio
async def test_get_newhigh_cache_returns_limited_cache_without_refresh(task):
    task._newhigh_cache = [{"code": "A"}, {"code": "B"}]

    with patch.object(task, "trigger_refresh") as mock_trigger:
        result = await task.get_newhigh_cache(limit=1)

    assert result == [{"code": "A"}]
    mock_trigger.assert_not_called()


def test_trigger_refresh_without_running_loop_returns_false(task):
    assert task.trigger_refresh() is False


@pytest.mark.asyncio
async def test_clear_refresh_task_handles_exception_and_resets_progress(task):
    done_task = MagicMock()
    done_task.result.side_effect = Exception("boom")
    with patch("asyncio.create_task", return_value=done_task):
        assert task.trigger_refresh() is True

    task._clear_refresh_task(done_task)

    assert task._refresh_task is None
    assert task._progress["running"] is False
    assert task._progress["status"] is None
    task._logger.error.assert_called_once()


def test_clear_refresh_task_ignores_cancelled_task(task):
    done_task = MagicMock()
    done_task.result.side_effect = asyncio.CancelledError()
    task._progress["running"] = True
    task._progress["status"] = "?좉퀬媛 媛깆떊 ?湲?以?.."

    task._clear_refresh_task(done_task)

    assert task._progress["running"] is False
    task._logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_load_snapshots_after_collector_still_empty_sets_error(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    mock_stock_repo.get_all_daily_snapshots.side_effect = [
        [_snap("005930", "?쇱꽦?꾩옄", 80000, 0)],
        [],
    ]

    result = await task_with_collector._load_snapshots_for_newhigh("20260413")

    assert result == []
    mock_daily_price_collector.force_run.assert_awaited_once()
    assert "20260413" in task_with_collector._progress["last_error"]


@pytest.mark.asyncio
async def test_write_newhigh_fields_writes_code_records(task, mock_stock_repo):
    mock_stock_repo.update_newhigh_fields = AsyncMock()
    stocks = [
        {"code": "005930", "is_historical_new_high": True},
        {"code": "", "is_historical_new_high": True},
        {"name": "no code"},
    ]

    await task._write_newhigh_fields("20260413", stocks)

    mock_stock_repo.update_newhigh_fields.assert_awaited_once_with(
        "20260413",
        [{"code": "005930", "is_newhigh": True, "is_historical_new_high": True}],
    )


@pytest.mark.asyncio
async def test_write_newhigh_fields_logs_warning_on_failure(task, mock_stock_repo):
    mock_stock_repo.update_newhigh_fields = AsyncMock(side_effect=Exception("db down"))

    await task._write_newhigh_fields("20260413", [{"code": "005930"}])

    task._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_notification_failure_still_marks_completed(task, mock_stock_repo, mock_notification_service):
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "?쇱꽦?꾩옄", 80000, 80000),
    ]
    mock_notification_service.emit.side_effect = Exception("notify down")

    await task._on_market_closed("20260413")

    assert task._last_collected_date == "20260413"
    assert task.get_progress()["newhigh_count"] == 1


@pytest.mark.asyncio
async def test_send_reports_without_notification_service(mock_stock_repo, mock_telegram_reporter):
    task = NewHighTask(
        stock_repo=mock_stock_repo,
        logger=MagicMock(),
        telegram_reporter=mock_telegram_reporter,
        notification_service=None,
    )

    await task._send_reports([{"code": "005930"}], "20260413", 1.2)

    mock_telegram_reporter.send_newhigh_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_newhigh_applies_rs_rating_service(
    task, mock_stock_repo, mock_stock_query_service, mock_rs_rating_service
):
    task._rs_rating_service = mock_rs_rating_service
    task._rs_rating_min = 80
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "?쇱꽦?꾩옄", 80000, 80000),
    ]
    mock_stock_query_service.get_ohlcv.return_value = MagicMock(
        rt_cd="0", data=[{"high": 70000}, {"high": 80000}]
    )
    mock_rs_rating_service.get_ratings_by_date.return_value = MagicMock(
        rt_cd="0", data={"005930": 90}
    )

    await task._run_newhigh("20260413")

    assert task._newhigh_cache[0]["rs_rating"] == 90
    mock_rs_rating_service.get_ratings_by_date.assert_awaited_once_with("20260413")


@pytest.mark.asyncio
async def test_enrich_and_filter_rs_rating_all_passes_no_filter_log(task, mock_rs_rating_service):
    task._rs_rating_service = mock_rs_rating_service
    task._rs_rating_min = 80
    mock_rs_rating_service.get_ratings_by_date.return_value = MagicMock(
        rt_cd="0", data={"005930": 85, "000660": 90}
    )
    stocks = [{"code": "005930"}, {"code": "000660"}]

    result = await task._enrich_and_filter_rs_rating(stocks, "20260413")

    assert [s["code"] for s in result] == ["005930", "000660"]
    task._logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_historical_high_keeps_stock_without_code(task, mock_stock_query_service):
    stocks = [{"name": "no code", "current_price": 2000}]

    enriched = await task._enrich_historical_high(stocks)

    assert enriched[0]["is_historical_new_high"] is False
    mock_stock_query_service.get_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_historical_high_logs_warning_on_exception(task, mock_stock_query_service):
    mock_stock_query_service.get_ohlcv.side_effect = Exception("ohlcv down")
    stocks = [{"code": "005930", "current_price": 2000}]

    enriched = await task._enrich_historical_high(stocks)

    assert enriched[0]["is_historical_new_high"] is False
    task._logger.warning.assert_called()


def test_filter_newhigh_rs_preserved_when_already_set(task):
    """스냅샷에 rs 값이 이미 있으면 rs_rating과 무관하게 기존 rs 값을 유지하는지 검증."""
    snap = _snap("005930", "삼성전자", 80000, 80000, market_cap=1_000_000_000)
    snap["rs"] = "custom_rs"
    snap["rs_rating"] = 90
    result = task._filter_newhigh([snap])
    assert len(result) == 1
    assert result[0]["rs"] == "custom_rs"


def test_filter_newhigh_rs_defaults_to_dash_when_rs_rating_is_none(task):
    """rs_rating=None인 스냅샷에서 rs 필드가 '-' 기본값으로 세팅되는지 검증."""
    snap = _snap("005930", "삼성전자", 80000, 80000, market_cap=1_000_000_000)
    snap["rs_rating"] = None
    result = task._filter_newhigh([snap])
    assert len(result) == 1
    assert result[0]["rs"] == "-"
