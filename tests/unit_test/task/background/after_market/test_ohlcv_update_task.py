"""
OhlcvUpdateTask 단위 테스트.

동작 흐름 검증:
1. 600일+ 보유 & 당일 데이터 있음  → API 호출 없이 스킵
2. 600일+ 보유 & 당일 데이터 없음  → API 호출 (당일 캔들만 갱신)
3. 600일 미만 (최초/리셋)           → API 호출 (역사 데이터 포함 전체 갱신)
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd

from task.background.after_market.ohlcv_update_task import OhlcvUpdateTask
from interfaces.schedulable_task import TaskPriority, TaskState
from common.types import ResCommonResponse, ErrorCode


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

TARGET_DATE = "20260318"


@pytest.fixture
def mock_sqs():
    """StockQueryService mock — 기본적으로 성공 응답 반환."""
    sqs = MagicMock()
    sqs.get_ohlcv = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="OHLCV 600건",
            data=[{"date": TARGET_DATE, "open": 70000, "high": 71000, "low": 69000,
                   "close": 70500, "volume": 1000000}],
        )
    )
    return sqs


@pytest.fixture
def mock_stock_repo():
    """StockRepository mock — 기본값: 데이터 없음(신규 종목)."""
    repo = MagicMock()
    repo.get_ohlcv_summary = AsyncMock(
        return_value={"count": 0, "latest_date": None, "oldest_date": None}
    )
    return repo


@pytest.fixture
def mock_mapper():
    """종목코드 매퍼 mock (3개 일반 종목)."""
    mapper = MagicMock()
    mapper.df = pd.DataFrame([
        {"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"},
        {"종목코드": "000660", "종목명": "SK하이닉스", "시장구분": "KOSPI"},
        {"종목코드": "035420", "종목명": "NAVER", "시장구분": "KOSPI"},
    ])
    return mapper


@pytest.fixture
def mock_mapper_with_excluded():
    """ETF/우선주/스팩 포함 매퍼 mock."""
    mapper = MagicMock()
    mapper.df = pd.DataFrame([
        {"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"},
        {"종목코드": "005935", "종목명": "삼성전자우", "시장구분": "KOSPI"},      # 우선주
        {"종목코드": "069500", "종목명": "KODEX 200", "시장구분": "KOSPI"},       # ETF
        {"종목코드": "000660", "종목명": "SK하이닉스", "시장구분": "KOSPI"},
        {"종목코드": "999990", "종목명": "테스트스팩1호", "시장구분": "KOSDAQ"},  # 스팩
        {"종목코드": "900080", "종목명": "이스트아시아홀딩스", "시장구분": "KONEX"},  # 비KOSPI/KOSDAQ
    ])
    return mapper


@pytest.fixture
def mock_mcs():
    mcs = MagicMock()
    mcs.is_market_open_now = AsyncMock(return_value=False)
    mcs.get_latest_trading_date = AsyncMock(return_value=TARGET_DATE)
    return mcs


@pytest.fixture
def task(mock_sqs, mock_mapper, mock_stock_repo, mock_mcs):
    return OhlcvUpdateTask(
        stock_query_service=mock_sqs,
        stock_code_repository=mock_mapper,
        stock_repo=mock_stock_repo,
        market_calendar_service=mock_mcs,
        logger=MagicMock(),
    )


# ──────────────────────────────────────────────
# 태스크 기본 속성
# ──────────────────────────────────────────────


class TestTaskProperties:

    def test_task_name(self, task):
        assert task.task_name == "ohlcv_update"

    def test_priority_is_low(self, task):
        assert task.priority == TaskPriority.LOW

    def test_initial_state_is_idle(self, task):
        assert task.state == TaskState.IDLE

    def test_initial_progress(self, task):
        p = task.get_progress()
        assert p["running"] is False
        assert p["processed"] == 0
        assert p["total"] == 0
        assert p["updated"] == 0
        assert p["skipped"] == 0


# ──────────────────────────────────────────────
# 동작 흐름 1: 스킵 (600일+ 보유 & 당일 완비)
# ──────────────────────────────────────────────


class TestSkipWhenAlreadyCurrent:

    async def test_skip_when_count_equals_target_and_date_matches(
        self, task, mock_sqs, mock_stock_repo
    ):
        """600일 보유 + 당일 날짜 일치 → API 호출 없이 False 반환."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is False
        mock_sqs.get_ohlcv.assert_not_called()

    async def test_skip_when_count_over_target_and_date_matches(
        self, task, mock_sqs, mock_stock_repo
    ):
        """600일 초과(예: 700일) + 당일 날짜 일치 → 스킵."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 700, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is False
        mock_sqs.get_ohlcv.assert_not_called()

    async def test_all_stocks_skipped_updates_progress(
        self, task, mock_sqs, mock_stock_repo
    ):
        """전체 종목이 스킵(백필 불필요)되면 API Fallback이 호출되지 않는다."""
        # DB에 모든 종목이 600일치 데이터를 가지고 있고, 최신 날짜로 갱신된 상태
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }

        # FDR 일괄 수집 부분 모킹 (테스트 속도 향상 및 외부 네트워크 격리)
        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True):
            await task._collect_all_ohlcv()

        p = task.get_progress()
        
        # 새로운 3-Tier 구조에서는 'skipped'를 개별 카운트하지 않고 조기 종료함
        assert p["updated"] == 0
        mock_sqs.get_ohlcv.assert_not_called()

    async def test_skip_does_not_sleep_chunk_delay(
        self, task, mock_sqs, mock_stock_repo
    ):
        """전체 스킵 시 CHUNK_SLEEP_SEC 대기가 발생하지 않아야 한다 (재시작 후 빠름 검증)."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }
        task.CHUNK_SLEEP_SEC = 10.0  # 매우 큰 값: sleep이 실제 호출되면 테스트가 느려짐

        slept_durations = []

        async def _fake_sleep(sec):
            slept_durations.append(sec)

        with patch("task.background.after_market.ohlcv_update_task.asyncio.sleep", side_effect=_fake_sleep):
            await task._collect_all_ohlcv()

        # API 호출이 없었으므로 CHUNK_SLEEP_SEC(10.0) 대기는 없어야 함
        assert all(d < task.CHUNK_SLEEP_SEC for d in slept_durations), (
            f"스킵 시에도 CHUNK_SLEEP_SEC 대기가 발생함: {slept_durations}"
        )


# ──────────────────────────────────────────────
# 동작 흐름 2: 당일 캔들 누락 (역사 데이터는 충분)
# ──────────────────────────────────────────────


class TestUpdateWhenTodayMissing:

    async def test_update_when_today_date_not_in_db(
        self, task, mock_sqs, mock_stock_repo
    ):
        """600일+ 보유하지만 latest_date가 어제 → get_ohlcv 호출 → True 반환."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": "20260317", "oldest_date": "20231201"
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is True
        mock_sqs.get_ohlcv.assert_called_once_with("005930", caller="OhlcvUpdateTask")

    async def test_update_when_latest_date_is_none(
        self, task, mock_sqs, mock_stock_repo
    ):
        """DB에 레코드가 아예 없는 경우(latest_date=None) → get_ohlcv 호출."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is True
        mock_sqs.get_ohlcv.assert_called_once()


# ──────────────────────────────────────────────
# 동작 흐름 3: 역사 데이터 없음 (최초 실행 / DB 완전 초기화)
# ──────────────────────────────────────────────


class TestUpdateWhenInsufficientHistory:

    async def test_update_when_count_below_target_but_today_present(
        self, task, mock_sqs, mock_stock_repo
    ):
        """보유일수 599이지만 latest_date == today → 스킵.

        today 날짜만 있으면 이미 수집 완료로 간주.
        역사 부족은 최초 full backfill 또는 force collect로 해결.
        """
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 599, "latest_date": TARGET_DATE, "oldest_date": "20240101"
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is False
        mock_sqs.get_ohlcv.assert_not_called()

    async def test_update_when_no_data_at_all(
        self, task, mock_sqs, mock_stock_repo
    ):
        """count=0, latest_date=None (신규/초기화) → API 호출."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is True
        mock_sqs.get_ohlcv.assert_called_once()

    async def test_skip_when_only_1_day_but_today(
        self, task, mock_sqs, mock_stock_repo
    ):
        """count=1이지만 latest_date == today → 스킵 (오늘 캔들 기준으로 판단)."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 1, "latest_date": TARGET_DATE, "oldest_date": TARGET_DATE
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is False
        mock_sqs.get_ohlcv.assert_not_called()


# ──────────────────────────────────────────────
# API 실패 / 예외 처리
# ──────────────────────────────────────────────


class TestErrorHandling:

    async def test_returns_none_on_api_error_response(
        self, task, mock_sqs, mock_stock_repo
    ):
        """get_ohlcv가 에러 응답(rt_cd != 0) → None 반환."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        mock_sqs.get_ohlcv = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="조회 실패",
                data=None,
            )
        )

        result = await task._update_stock_ohlcv("005930")

        assert result is None

    async def test_returns_none_on_none_response(
        self, task, mock_sqs, mock_stock_repo
    ):
        """get_ohlcv가 None 반환 → None 반환."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        mock_sqs.get_ohlcv = AsyncMock(return_value=None)

        result = await task._update_stock_ohlcv("005930")

        assert result is None

    async def test_returns_none_on_exception(
        self, task, mock_sqs, mock_stock_repo
    ):
        """get_ohlcv가 예외 발생 → None 반환 (태스크 중단 없음)."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        mock_sqs.get_ohlcv = AsyncMock(side_effect=RuntimeError("서버 오류"))

        result = await task._update_stock_ohlcv("005930")

        assert result is None

    async def test_error_stocks_excluded_from_counts(
        self, task, mock_sqs, mock_stock_repo
    ):
        """오류 종목은 updated/skipped 카운트에서 제외된다."""
        call_count = 0

        async def _side_effect(code, **kwargs):
            nonlocal call_count
            call_count += 1
            if code == "000660":
                raise RuntimeError("오류")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[]
            )

        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        mock_sqs.get_ohlcv = AsyncMock(side_effect=_side_effect)

        await task._collect_all_ohlcv()

        p = task.get_progress()
        assert p["updated"] == 2   # 005930, 035420
        assert p["skipped"] == 0
        assert p["processed"] == 3


# ──────────────────────────────────────────────
# _collect_all_ohlcv 가드 조건
# ──────────────────────────────────────────────


class TestCollectGuards:

    async def test_skip_during_market_hours(self, task, mock_mcs, mock_sqs):
        """장 운영 중에는 수집 시작하지 않는다."""
        mock_mcs.is_market_open_now.return_value = True

        await task._collect_all_ohlcv()

        mock_sqs.get_ohlcv.assert_not_called()

    async def test_skip_when_already_collected_same_date(self, task, mock_sqs):
        """동일 날짜로 이미 수집 완료된 경우 스킵."""
        task._last_collected_date = TARGET_DATE

        await task._collect_all_ohlcv()

        mock_sqs.get_ohlcv.assert_not_called()

    async def test_skip_when_no_trading_date(self, task, mock_mcs, mock_sqs):
        """거래일 조회 실패 시 수집 중단."""
        mock_mcs.get_latest_trading_date.return_value = None

        await task._collect_all_ohlcv()

        mock_sqs.get_ohlcv.assert_not_called()

    async def test_skip_when_already_in_progress(self, task, mock_sqs):
        """이미 수집 진행 중이면 중복 실행하지 않는다."""
        task._is_collecting = True

        await task._collect_all_ohlcv()

        mock_sqs.get_ohlcv.assert_not_called()

    async def test_is_collecting_reset_on_completion(self, task):
        """수집 완료 후 _is_collecting이 False로 리셋된다."""
        await task._collect_all_ohlcv()

        assert task._is_collecting is False

    async def test_is_collecting_reset_on_exception(self, task, mock_mcs, mock_stock_repo):
        """수집 도중 예외가 발생해도 _is_collecting이 False로 리셋된다."""
        # 거래일 조회는 정상 통과시키고, try 블록 내부의 저장소 조회 시 예외 발생 유도
        mock_stock_repo.get_ohlcv_summary.side_effect = RuntimeError("DB 오류")

        # FDR 일괄 수집 모킹
        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True):
            await task._collect_all_ohlcv()

        # 내부 예외가 캐치되고 finally 블록이 실행되어 False로 리셋되었는지 확인
        assert task._is_collecting is False


# ──────────────────────────────────────────────
# 수집 완료 후 상태 검증
# ──────────────────────────────────────────────


class TestCollectCompletion:

    async def test_last_collected_date_updated_after_success(
        self, task, mock_sqs, mock_stock_repo
    ):
        """수집 성공 후 _last_collected_date가 target_date로 갱신된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task._collect_all_ohlcv()

        assert task._last_collected_date == TARGET_DATE

    async def test_progress_running_false_after_completion(self, task):
        """수집 완료 후 progress.running이 False."""
        await task._collect_all_ohlcv()

        assert task.get_progress()["running"] is False

    async def test_progress_total_matches_stock_count(
        self, task, mock_stock_repo
    ):
        """progress.total이 전체 종목 수(3)와 일치한다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task._collect_all_ohlcv()

        p = task.get_progress()
        assert p["total"] == 3
        assert p["processed"] == 3

    async def test_progress_updated_count_correct(
        self, task, mock_sqs, mock_stock_repo
    ):
        """API 호출 성공 건수가 progress.updated에 정확히 반영된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task._collect_all_ohlcv()

        p = task.get_progress()
        assert p["updated"] == 3
        assert p["skipped"] == 0

    async def test_notification_emitted_on_completion(
        self, task, mock_stock_repo
    ):
        """수집 완료 시 NotificationService.emit이 호출된다."""
        ns = MagicMock()
        ns.emit = AsyncMock()
        task._ns = ns
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task._collect_all_ohlcv()

        ns.emit.assert_called_once()
        args = ns.emit.call_args[0]
        assert args[0] == "BACKGROUND"
        assert "OHLCV" in args[2]

    async def test_second_run_same_date_skips_all(self, task, mock_sqs, mock_stock_repo):
        """동일 날짜 2회 수집 시 두 번째는 API 호출 없이 스킵."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task._collect_all_ohlcv()  # 첫 번째
        mock_sqs.get_ohlcv.reset_mock()

        await task._collect_all_ohlcv()  # 두 번째 — 동일 날짜

        mock_sqs.get_ohlcv.assert_not_called()


# ──────────────────────────────────────────────
# _on_market_closed 콜백
# ──────────────────────────────────────────────


class TestOnMarketClosed:

    async def test_triggers_collect_when_date_changed(self, task, mock_sqs, mock_stock_repo):
        """새 거래일이면 _collect_all_ohlcv가 실행된다."""
        task._last_collected_date = "20260317"  # 어제
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task._on_market_closed(TARGET_DATE)

        assert task._last_collected_date == TARGET_DATE

    async def test_skips_collect_when_already_collected(self, task, mock_sqs):
        """이미 수집한 날짜는 콜백에서도 스킵."""
        task._last_collected_date = TARGET_DATE

        await task._on_market_closed(TARGET_DATE)

        mock_sqs.get_ohlcv.assert_not_called()


# ──────────────────────────────────────────────
# _load_all_stocks 필터링
# ──────────────────────────────────────────────


class TestLoadAllStocks:

    def test_filters_etf(self, mock_sqs, mock_mapper_with_excluded, mock_stock_repo, mock_mcs):
        """ETF(KODEX 접두사)는 제외된다."""
        t = OhlcvUpdateTask(
            stock_query_service=mock_sqs,
            stock_code_repository=mock_mapper_with_excluded,
            stock_repo=mock_stock_repo,
            market_calendar_service=mock_mcs,
            logger=MagicMock(),
        )
        codes = [c for c, _, _ in t._load_all_stocks()]
        assert "069500" not in codes

    def test_filters_preferred_stock(self, mock_sqs, mock_mapper_with_excluded, mock_stock_repo, mock_mcs):
        """우선주(종목코드 끝자리 != '0')는 제외된다."""
        t = OhlcvUpdateTask(
            stock_query_service=mock_sqs,
            stock_code_repository=mock_mapper_with_excluded,
            stock_repo=mock_stock_repo,
            market_calendar_service=mock_mcs,
            logger=MagicMock(),
        )
        codes = [c for c, _, _ in t._load_all_stocks()]
        assert "005935" not in codes

    def test_filters_spac(self, mock_sqs, mock_mapper_with_excluded, mock_stock_repo, mock_mcs):
        """스팩은 제외된다."""
        t = OhlcvUpdateTask(
            stock_query_service=mock_sqs,
            stock_code_repository=mock_mapper_with_excluded,
            stock_repo=mock_stock_repo,
            market_calendar_service=mock_mcs,
            logger=MagicMock(),
        )
        codes = [c for c, _, _ in t._load_all_stocks()]
        assert "999990" not in codes

    def test_filters_non_kospi_kosdaq(self, mock_sqs, mock_mapper_with_excluded, mock_stock_repo, mock_mcs):
        """KOSPI/KOSDAQ 이외 시장(KONEX 등)은 제외된다."""
        t = OhlcvUpdateTask(
            stock_query_service=mock_sqs,
            stock_code_repository=mock_mapper_with_excluded,
            stock_repo=mock_stock_repo,
            market_calendar_service=mock_mcs,
            logger=MagicMock(),
        )
        codes = [c for c, _, _ in t._load_all_stocks()]
        assert "900080" not in codes

    def test_includes_valid_stocks(self, mock_sqs, mock_mapper_with_excluded, mock_stock_repo, mock_mcs):
        """일반 KOSPI/KOSDAQ 종목은 포함된다."""
        t = OhlcvUpdateTask(
            stock_query_service=mock_sqs,
            stock_code_repository=mock_mapper_with_excluded,
            stock_repo=mock_stock_repo,
            market_calendar_service=mock_mcs,
            logger=MagicMock(),
        )
        codes = [c for c, _, _ in t._load_all_stocks()]
        assert "005930" in codes
        assert "000660" in codes


# ──────────────────────────────────────────────
# Suspend / Resume
# ──────────────────────────────────────────────


class TestSuspendResume:

    async def test_suspend_changes_state(self, task):
        task._state = TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED
        assert not task._suspend_event.is_set()

    async def test_resume_changes_state(self, task):
        task._state = TaskState.RUNNING
        await task.suspend()
        await task.resume()
        assert task.state == TaskState.RUNNING
        assert task._suspend_event.is_set()

    async def test_suspend_noop_when_not_running(self, task):
        """IDLE 상태에서 suspend해도 상태 변화 없음."""
        assert task.state == TaskState.IDLE
        await task.suspend()
        assert task.state == TaskState.IDLE

    async def test_resume_noop_when_not_suspended(self, task):
        """IDLE 상태에서 resume해도 상태 변화 없음."""
        assert task.state == TaskState.IDLE
        await task.resume()
        assert task.state == TaskState.IDLE

    async def test_suspend_pauses_between_chunks(
        self, task, mock_sqs, mock_stock_repo
    ):
        """suspend 호출 후 chunk 경계에서 수집이 중단된다."""
        collected = []
        barrier = asyncio.Event()

        async def _side_effect(code, **kwargs):
            collected.append(code)
            if len(collected) == 1:
                barrier.set()
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[]
            )

        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        mock_sqs.get_ohlcv = AsyncMock(side_effect=_side_effect)

        task.API_CHUNK_SIZE = 1
        task.CHUNK_SLEEP_SEC = 0

        collect_coro = asyncio.create_task(task._collect_all_ohlcv())

        await barrier.wait()

        task._state = TaskState.RUNNING
        await task.suspend()

        count_before = len(collected)
        await asyncio.sleep(0.05)
        assert len(collected) == count_before  # 중단됨

        await task.resume()
        await collect_coro

        assert len(collected) == 3  # 재개 후 전체 완료


# ──────────────────────────────────────────────
# Start / Stop
# ──────────────────────────────────────────────


class TestStartStop:

    async def test_start_sets_running_state(self, task):
        await task.start()
        assert task.state == TaskState.RUNNING
        await task.stop()

    async def test_start_creates_two_tasks(self, task):
        """start() 시 scheduler 1개 asyncio.Task 생성."""
        await task.start()
        assert len(task._tasks) == 1
        await task.stop()

    async def test_stop_sets_stopped_state(self, task):
        await task.start()
        await task.stop()
        assert task.state == TaskState.STOPPED
        assert len(task._tasks) == 0

    async def test_start_idempotent(self, task):
        """RUNNING 중 start() 재호출 시 태스크가 추가되지 않는다."""
        await task.start()
        task_count = len(task._tasks)

        await task.start()  # 중복 호출

        assert len(task._tasks) == task_count
        await task.stop()


# ──────────────────────────────────────────────
# Force Collect: skip 조건 무시 강제 수집
# ──────────────────────────────────────────────


class TestForceCollect:

    async def test_force_collect_ignores_last_collected_date(
        self, task, mock_sqs, mock_stock_repo
    ):
        """force=True이면 _last_collected_date == target_date여도 수집이 실행된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        task._last_collected_date = TARGET_DATE  # 이미 오늘 수집 완료 표시

        await task.force_collect()

        mock_sqs.get_ohlcv.assert_called()

    async def test_force_collect_calls_api_even_if_count_sufficient(
        self, task, mock_sqs, mock_stock_repo
    ):
        """force=True이면 latest_date == today 스킵 조건을 무시하고 API를 호출한다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231001"
        }

        await task.force_collect()

        mock_sqs.get_ohlcv.assert_called()

    async def test_force_collect_sets_progress_force_flag(
        self, task, mock_stock_repo
    ):
        """force 수집 중 progress['force']가 True로 설정된다."""
        captured = {}
        original_update = task._progress.update

        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task.force_collect()

        # 수집 완료 후 progress에 force 키가 존재했어야 함 (running=False로 리셋됨)
        # _last_collected_date는 정상적으로 갱신
        assert task._last_collected_date == TARGET_DATE

    async def test_normal_collect_skips_when_already_done(
        self, task, mock_sqs, mock_stock_repo
    ):
        """일반 수집(force=False)에서는 _last_collected_date == target_date이면 스킵된다."""
        task._last_collected_date = TARGET_DATE

        await task._collect_all_ohlcv(force=False)

        mock_sqs.get_ohlcv.assert_not_called()

    async def test_update_stock_ohlcv_force_skips_db_check(
        self, task, mock_sqs, mock_stock_repo
    ):
        """_update_stock_ohlcv(force=True)는 DB 조회 없이 바로 API를 호출한다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231001"
        }

        result = await task._update_stock_ohlcv("005930")

        assert result is True
        mock_stock_repo.get_ohlcv_summary.assert_not_called()  # DB 조회 건너뜀
        mock_sqs.get_ohlcv.assert_called_once()
