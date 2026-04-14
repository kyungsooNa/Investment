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
    collector.force_collect = AsyncMock()
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


# ── force_collect 트리거 시나리오 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_collect_triggered_when_w52_missing(
    task_with_collector, mock_stock_repo, mock_daily_price_collector, mock_telegram_reporter
):
    """w52_high 데이터 부재 시 force_collect 호출 후 재조회하여 신고가 탐색."""
    mock_stock_repo.get_all_daily_snapshots.side_effect = [
        # 1차 조회: w52_high 없음 (FDR 수집 케이스)
        [_snap("005930", "삼성전자", 80000, 0, market_cap=500_000_000_000)],
        # 2차 조회 (force_collect 후): w52_high 복원
        [_snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000)],
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_collect.assert_awaited_once()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 2
    stocks = mock_telegram_reporter.send_newhigh_report.call_args[0][0]
    assert stocks[0]["code"] == "005930"


@pytest.mark.asyncio
async def test_force_collect_not_triggered_when_w52_present(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """w52_high 데이터 충분 시 force_collect 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000),
        _snap("000660", "SK하이닉스", 50000, 50000, market_cap=300_000_000_000),
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_collect.assert_not_awaited()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 1


@pytest.mark.asyncio
async def test_force_collect_not_triggered_without_collector(task, mock_stock_repo):
    """daily_price_collector_task 없으면 w52_high 부재여도 force_collect 없이 정상 완료."""
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
async def test_force_collect_retries_only_once(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """force_collect 후 재조회한 데이터에 여전히 w52_high 없어도 두 번째 force_collect 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 0),
    ]
    await task_with_collector._on_market_closed("20260412")

    # force_collect 는 한 번만
    mock_daily_price_collector.force_collect.assert_awaited_once()
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
    collector.force_collect = AsyncMock()
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


# ── force_collect 트리거 시나리오 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_collect_triggered_when_w52_missing(
    task_with_collector, mock_stock_repo, mock_daily_price_collector, mock_telegram_reporter
):
    """w52_high 데이터 부재 시 force_collect 호출 후 재조회하여 신고가 탐색."""
    mock_stock_repo.get_all_daily_snapshots.side_effect = [
        # 1차 조회: w52_high 없음 (FDR 수집 케이스)
        [_snap("005930", "삼성전자", 80000, 0, market_cap=500_000_000_000)],
        # 2차 조회 (force_collect 후): w52_high 복원
        [_snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000)],
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_collect.assert_awaited_once()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 2
    stocks = mock_telegram_reporter.send_newhigh_report.call_args[0][0]
    assert stocks[0]["code"] == "005930"


@pytest.mark.asyncio
async def test_force_collect_not_triggered_when_w52_present(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """w52_high 데이터 충분 시 force_collect 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 80000, market_cap=500_000_000_000),
        _snap("000660", "SK하이닉스", 50000, 50000, market_cap=300_000_000_000),
    ]
    await task_with_collector._on_market_closed("20260412")

    mock_daily_price_collector.force_collect.assert_not_awaited()
    assert mock_stock_repo.get_all_daily_snapshots.await_count == 1


@pytest.mark.asyncio
async def test_force_collect_not_triggered_without_collector(task, mock_stock_repo):
    """daily_price_collector_task 없으면 w52_high 부재여도 force_collect 없이 정상 완료."""
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
async def test_force_collect_retries_only_once(
    task_with_collector, mock_stock_repo, mock_daily_price_collector
):
    """force_collect 후 재조회한 데이터에 여전히 w52_high 없어도 두 번째 force_collect 미호출."""
    mock_stock_repo.get_all_daily_snapshots.return_value = [
        _snap("005930", "삼성전자", 80000, 0),
    ]
    await task_with_collector._on_market_closed("20260412")

    # force_collect 는 한 번만
    mock_daily_price_collector.force_collect.assert_awaited_once()
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
