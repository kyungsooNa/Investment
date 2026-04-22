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
from types import SimpleNamespace
import pandas as pd

from task.background.after_market.ohlcv_update_task import OhlcvUpdateTask
from interfaces.schedulable_task import TaskPriority, TaskState
from common.types import ResCommonResponse, ErrorCode
from services.notification_service import NotificationCategory, NotificationLevel


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
        """600일 보유 + 당일 날짜 일치 → 백필 없이 스킵된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }

        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True), \
             patch.object(task, '_backfill_historical_data') as mock_backfill:
            await task._collect_all_ohlcv()
            
            # 조건이 만족되었으므로 백필 로직이 아예 호출되지 않아야 함
            mock_backfill.assert_not_called()

    async def test_skip_when_count_over_target_and_date_matches(
        self, task, mock_sqs, mock_stock_repo
    ):
        """600일 초과(예: 700일) + 당일 날짜 일치 → 백필 없이 스킵된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 700, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }

        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True), \
             patch.object(task, '_backfill_historical_data') as mock_backfill:
            await task._collect_all_ohlcv()
            
            mock_backfill.assert_not_called()

    async def test_all_stocks_skipped_updates_progress(
        self, task, mock_sqs, mock_stock_repo
    ):
        """전체 종목이 스킵(백필 불필요)되면 조기 종료된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }

        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True):
            await task._collect_all_ohlcv()

        p = task.get_progress()
        assert p["updated"] == 0
        mock_sqs.get_ohlcv.assert_not_called()

    async def test_skip_does_not_sleep_chunk_delay(
        self, task, mock_sqs, mock_stock_repo
    ):
        """전체 스킵 시 CHUNK_SLEEP_SEC 대기가 발생하지 않아야 한다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231201"
        }
        task.CHUNK_SLEEP_SEC = 10.0

        slept_durations = []

        async def _fake_sleep(sec):
            slept_durations.append(sec)

        with patch("task.background.after_market.ohlcv_update_task.asyncio.sleep", side_effect=_fake_sleep), \
             patch.object(task, '_try_daily_bulk_via_fdr', return_value=True):
            await task._collect_all_ohlcv()

        assert all(d < task.CHUNK_SLEEP_SEC for d in slept_durations)


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
        """보유일수 10일 이상 600일 미만이고 latest_date == today → 백필 스킵.
        (오늘 날짜가 있으면 정상 수집된 것으로 간주)
        """
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 599, "latest_date": TARGET_DATE, "oldest_date": "20240101"
        }

        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True), \
             patch.object(task, '_backfill_historical_data') as mock_backfill:
            await task._collect_all_ohlcv()
            
            mock_backfill.assert_not_called()

    async def test_update_when_no_data_at_all(
        self, task, mock_sqs, mock_stock_repo
    ):
        """count=0, latest_date=None (신규/초기화) → 백필 수행."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True), \
             patch.object(task, '_backfill_historical_data') as mock_backfill:
            await task._collect_all_ohlcv()
            
            mock_backfill.assert_called_once()

    # ★ 리뷰 반영: count=1 이면 무조건 백필을 수행하는 것이 맞으므로 로직 및 함수명 수정
    async def test_backfill_when_only_1_day_but_today(
        self, task, mock_sqs, mock_stock_repo
    ):
        """count < 10 이면 latest_date == today 여도 신규/빈 DB로 간주하여 백필 수행."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 1, "latest_date": TARGET_DATE, "oldest_date": TARGET_DATE
        }
        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True), \
             patch.object(task, '_backfill_historical_data') as mock_backfill:
            await task._collect_all_ohlcv()
            mock_backfill.assert_called_once()

# ──────────────────────────────────────────────
# 3-Tier 파이프라인 상호작용 및 Fallback 검증
# ──────────────────────────────────────────────

class TestThreeTierPipelineInteraction:

    async def test_tier1_success_skips_tier2_and_tier3(
        self, task, mock_sqs, mock_stock_repo
    ):
        """[시나리오 1] Tier 1(일괄 수집) 성공 & 모든 종목 DB 완비 → Tier 2/3 완전 스킵"""
        # 모든 종목이 600일치 데이터를 가지고 있고, 오늘 날짜로 갱신됨
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20230101"
        }

        # Tier 1 성공 모킹
        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True) as mock_tier1, \
             patch.object(task, '_backfill_historical_data') as mock_tier2_3:
            
            await task._collect_all_ohlcv()
            
            mock_tier1.assert_called_once()
            mock_tier2_3.assert_not_called()  # 백필 완전 스킵 검증

    async def test_partial_tier2_backfill_routing(
        self, task, mock_sqs, mock_stock_repo
    ):
        """[시나리오 2] Tier 1 성공 후, 일부 종목(신규 상장 등)만 필터링되어 Tier 2로 넘어감"""
        
        async def _mock_summary(code):
            if code == "000660":
                # SK하이닉스는 데이터가 5개밖에 없음 -> 백필 대상
                return {"count": 5, "latest_date": TARGET_DATE, "oldest_date": TARGET_DATE}
            # 나머지는 완비 -> 스킵 대상
            return {"count": 600, "latest_date": TARGET_DATE, "oldest_date": "20230101"}

        mock_stock_repo.get_ohlcv_summary.side_effect = _mock_summary

        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=True), \
             patch.object(task, '_backfill_historical_data') as mock_tier2_3:
            
            await task._collect_all_ohlcv()
            
            # 백필 로직이 호출되었는지 검증
            mock_tier2_3.assert_called_once()
            
            # 백필 대상으로 넘어간 종목 리스트 확인
            needs_backfill_stocks = mock_tier2_3.call_args[0][0]
            assert len(needs_backfill_stocks) == 1
            assert needs_backfill_stocks[0][0] == "000660"  # SK하이닉스만 넘어갔는지 확인

    async def test_tier2_fdr_failure_falls_back_to_tier3(
        self, task, mock_sqs, mock_stock_repo
    ):
        """[시나리오 3] Tier 2(FDR 고속 백필) 과정에서 에러 발생 시 Tier 3(API 우회)로 Fallback 됨"""
        needs_backfill = [("005930", "삼성전자", "KOSPI")]
        
        # FDR DataReader가 에러를 발생시키도록 모킹
        def _fdr_error(*args, **kwargs):
            raise ConnectionError("네이버 금융 서버 응답 없음")

        with patch('task.background.after_market.ohlcv_update_task.fdr.DataReader', side_effect=_fdr_error), \
             patch.object(task, '_update_stock_ohlcv', return_value=True) as mock_tier3:
            
            await task._backfill_historical_data(needs_backfill, TARGET_DATE, False, time.time())
            
            # Tier 2가 실패했으므로 Tier 3(증권사 API 개별 호출)이 실행되어야 함
            mock_tier3.assert_called_once_with("005930")

    async def test_tier2_sanity_check_failure_falls_back_to_tier3(
        self, task, mock_sqs, mock_stock_repo
    ):
        """[시나리오 4] Tier 2 자체 검증(Sanity Check) 실패 시 수정주가 오류로 간주하고 Tier 3로 Fallback 됨"""
        needs_backfill = [("005930", "삼성전자", "KOSPI")]
        
        # FDR은 데이터를 정상적으로 가져왔다고 가정
        fake_fdr_df = pd.DataFrame([
            {"Open": 70000, "High": 71000, "Low": 69000, "Close": 70500, "Volume": 100}
        ], index=[pd.to_datetime(TARGET_DATE)])
        
        # DB에는 완전히 다른 종가(수정주가 미반영 상태 가정)가 들어있다고 모킹
        mock_stock_repo.get_stock_data = AsyncMock(return_value={
            "ohlcv": [{"date": TARGET_DATE, "close": 50000}] # FDR(70500) != DB(50000)
        })

        with patch('task.background.after_market.ohlcv_update_task.fdr.DataReader', return_value=fake_fdr_df), \
             patch.object(task, '_update_stock_ohlcv', return_value=True) as mock_tier3:
            
            await task._backfill_historical_data(needs_backfill, TARGET_DATE, False, time.time())
            
            # Sanity Check가 실패했으므로 Tier 3(증권사 API)가 호출되어야 함
            mock_tier3.assert_called_once_with("005930")
            # 잘못된 데이터가 DB에 들어가면 안 됨
            mock_stock_repo.upsert_ohlcv.assert_not_called()

# ──────────────────────────────────────────────
# API 실패 / 예외 처리
# ──────────────────────────────────────────────


class TestErrorHandling:

    # ★ 리뷰 반영: _update_stock_ohlcv 호출 시 파라미터 1개(code)만 전달하도록 수정
    async def test_returns_none_on_api_error_response(self, task, mock_sqs, mock_stock_repo):
        mock_stock_repo.get_ohlcv_summary.return_value = {"count": 0, "latest_date": None, "oldest_date": None}
        mock_sqs.get_ohlcv = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None))
        result = await task._update_stock_ohlcv("005930")
        assert result is None

    async def test_returns_none_on_none_response(self, task, mock_sqs, mock_stock_repo):
        mock_stock_repo.get_ohlcv_summary.return_value = {"count": 0, "latest_date": None, "oldest_date": None}
        mock_sqs.get_ohlcv = AsyncMock(return_value=None)
        result = await task._update_stock_ohlcv("005930")
        assert result is None

    async def test_returns_none_on_exception(self, task, mock_sqs, mock_stock_repo):
        mock_stock_repo.get_ohlcv_summary.return_value = {"count": 0, "latest_date": None, "oldest_date": None}
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

        # 👇 수정: FDR 로직 우회
        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=False), \
             patch('task.background.after_market.ohlcv_update_task.fdr.DataReader', side_effect=Exception("FDR 우회")):
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

        # 👇 수정: Tier 1과 Tier 2를 강제 실패시켜 Tier 3(mock_sqs)가 호출되도록 유도
        with patch.object(task, '_try_daily_bulk_via_fdr', return_value=False), \
             patch('task.background.after_market.ohlcv_update_task.fdr.DataReader', side_effect=Exception("FDR 우회")):
             
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

    async def test_force_run_ignores_last_collected_date(
        self, task, mock_sqs, mock_stock_repo
    ):
        """force=True이면 _last_collected_date == target_date여도 수집이 실행된다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }
        task._last_collected_date = TARGET_DATE  # 이미 오늘 수집 완료 표시

        await task.force_run()

        mock_sqs.get_ohlcv.assert_called()

    async def test_force_run_calls_api_even_if_count_sufficient(
        self, task, mock_sqs, mock_stock_repo
    ):
        """force=True이면 latest_date == today 스킵 조건을 무시하고 API를 호출한다."""
        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 600, "latest_date": TARGET_DATE, "oldest_date": "20231001"
        }

        await task.force_run()

        mock_sqs.get_ohlcv.assert_called()

    async def test_force_run_sets_progress_force_flag(
        self, task, mock_stock_repo
    ):
        """force 수집 중 progress['force']가 True로 설정된다."""
        captured = {}
        original_update = task._progress.update

        mock_stock_repo.get_ohlcv_summary.return_value = {
            "count": 0, "latest_date": None, "oldest_date": None
        }

        await task.force_run()

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
        
    async def test_update_stock_ohlcv_skips_db_check(
        self, task, mock_sqs, mock_stock_repo
    ):
        """_update_stock_ohlcv는 이제 항상 API를 직접 호출한다."""
        result = await task._update_stock_ohlcv("005930")

        assert result is True
        mock_stock_repo.get_ohlcv_summary.assert_not_called()  # DB 조회가 아예 없어야 함
        mock_sqs.get_ohlcv.assert_called_once()


# ──────────────────────────────────────────────
# 추가된 Coverage 보완용 테스트 케이스
# ──────────────────────────────────────────────

class TestFormatRecordsEdgeCases:
    def test_format_fdr_listing_to_ohlcv_records_amount_fallback(self, task):
        """Amount(거래대금)가 NaN이거나 0일 때 종가*거래량으로 대체되는지 확인"""
        df = pd.DataFrame([
            {"Code": "005930", "Close": 70000, "Open": 69000, "High": 71000, "Low": 68000, "Volume": 100, "Amount": pd.NA},
            {"Code": "000660", "Close": 150000, "Open": 140000, "High": 160000, "Low": 130000, "Volume": 200, "Amount": 0}
        ])
        valid_codes = {"005930", "000660"}
        records = task._format_fdr_listing_to_ohlcv_records(df, "20260318", valid_codes)
        assert len(records) == 2
        assert records[0]["trading_value"] == 70000 * 100
        assert records[1]["trading_value"] == 150000 * 200

    def test_format_fdr_listing_to_ohlcv_records_invalid_type(self, task):
        """숫자 변환 불가능한 값이 섞여 있을 때 스킵(예외처리)되는지 확인"""
        df = pd.DataFrame([
            {"Code": "005930", "Close": "invalid", "Volume": 100},
            {"Code": "000660", "Close": 150000, "Volume": 200, "Amount": 30000000}
        ])
        records = task._format_fdr_listing_to_ohlcv_records(df, "20260318", {"005930", "000660"})
        assert len(records) == 1
        assert records[0]["code"] == "000660"

    def test_format_fdr_to_ohlcv_records_invalid_type(self, task):
        """과거 데이터(시계열) 포맷팅 중 형변환 에러 시 해당 행 스킵 확인"""
        df = pd.DataFrame([
            {"Open": 100, "High": 110, "Low": 90, "Close": "bad_data", "Volume": 10},
            {"Open": 100, "High": 110, "Low": 90, "Close": 105, "Volume": 10}
        ], index=[pd.to_datetime("20260317"), pd.to_datetime("20260318")])
        records = task._format_fdr_to_ohlcv_records("005930", df)
        assert len(records) == 1
        assert records[0]["date"] == "20260318"

class TestTryDailyBulkViaFDR:
    async def test_returns_false_on_empty_df(self, task):
        """DataFrame이 비어있을 때 False를 리턴하는지 확인"""
        with patch('asyncio.to_thread', new_callable=AsyncMock, return_value=pd.DataFrame()):
            assert await task._try_daily_bulk_via_fdr(TARGET_DATE, time.time(), {"005930"}) is False
            
    async def test_returns_false_on_verify_fail(self, task):
        """_verify_crawler_data 정합성 검증 실패 시 False 리턴 확인"""
        df = pd.DataFrame([{"Code": "005930", "Close": 70000}])
        with patch('asyncio.to_thread', new_callable=AsyncMock, return_value=df), \
             patch.object(task, '_verify_crawler_data', return_value=False):
            assert await task._try_daily_bulk_via_fdr(TARGET_DATE, time.time(), {"005930"}) is False

    async def test_returns_false_on_empty_records(self, task):
        """DF에서 추출한 record가 비어있을 때 False 리턴 확인"""
        df = pd.DataFrame([{"Code": "005930", "Close": 70000}])
        with patch('asyncio.to_thread', new_callable=AsyncMock, return_value=df), \
             patch.object(task, '_verify_crawler_data', return_value=True), \
             patch.object(task, '_format_fdr_listing_to_ohlcv_records', return_value=[]):
            assert await task._try_daily_bulk_via_fdr(TARGET_DATE, time.time(), {"005930"}) is False
            
    async def test_returns_false_on_exception(self, task):
        """스레드 내부 등에서 예외 발생 시 False 반환 및 로깅 확인"""
        with patch('asyncio.to_thread', new_callable=AsyncMock, side_effect=Exception("FDR Error")):
            assert await task._try_daily_bulk_via_fdr(TARGET_DATE, time.time(), {"005930"}) is False

class TestNotificationAndProgressEdgeCases:
    async def test_exception_emits_notification(self, task, mock_mcs, mock_sqs):
        """전체 수집 파이프라인 수행 중 예외 발생 시 Notification이 에러 레벨로 전송되는지 검증"""
        ns = MagicMock()
        ns.emit = AsyncMock()
        task._ns = ns
        
        # 의도적으로 에러 유발 (try-except 블록 내부에서 호출되는 메서드를 패치)
        with patch.object(task, '_try_daily_bulk_via_fdr', side_effect=Exception("FDR Error")):
            await task._collect_all_ohlcv()
            
        ns.emit.assert_called_once()
        args = ns.emit.call_args[0]
        assert args[0] == NotificationCategory.BACKGROUND
        assert args[1] == NotificationLevel.ERROR
        assert "OHLCV 파이프라인 실패" in args[2]

    async def test_save_bulk_to_db_with_progress_empty(self, task):
        """빈 records가 전달되었을 때 바로 리턴하는지 확인"""
        initial_updated = task._progress.get("updated", 0)
        await task._save_bulk_to_db_with_progress([], time.time())
        assert task._progress.get("updated", 0) == initial_updated # 진행률 변경 없음

    async def test_verify_crawler_data_missing_canary_stock(self, task, mock_sqs):
        """크롤링 데이터에 카나리 종목이 존재하지 않아 KeyError/IndexError가 발생하는 경우 검증 실패 처리 확인"""
        # await 호출 시 TypeError 방지를 위해 get_current_price를 AsyncMock으로 설정
        mock_sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {}})
        )
        # 카나리 종목이 없는 크롤링 데이터
        df_crawled = pd.DataFrame({"Code": ["999999"], "Close": [1000]})
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is False


class TestAdditionalCoverage:
    def test_scheduler_label(self, task):
        assert task._scheduler_label == "OhlcvUpdate"

    async def test_try_daily_bulk_via_fdr_success_saves_records(self, task):
        df = pd.DataFrame([{"Code": "005930", "Close": 70000}])
        records = [{"code": "005930", "date": TARGET_DATE, "close": 70000}]
        start_time = time.time()

        with patch("task.background.after_market.ohlcv_update_task.asyncio.to_thread", new=AsyncMock(return_value=df)), \
             patch.object(task, "_verify_crawler_data", new=AsyncMock(return_value=True)), \
             patch.object(task, "_format_fdr_listing_to_ohlcv_records", return_value=records), \
             patch.object(task, "_save_bulk_to_db_with_progress", new=AsyncMock()) as mock_save:
            result = await task._try_daily_bulk_via_fdr(TARGET_DATE, start_time, {"005930"})

        assert result is True
        mock_save.assert_awaited_once_with(records, start_time)

    async def test_backfill_upserts_when_db_empty_without_api_fallback(
        self, task, mock_stock_repo
    ):
        df = pd.DataFrame(
            [{"Open": 100, "High": 110, "Low": 90, "Close": 105, "Volume": 10}],
            index=[pd.to_datetime(TARGET_DATE)],
        )
        mock_stock_repo.upsert_ohlcv = AsyncMock()
        mock_stock_repo.get_stock_data = AsyncMock(return_value=None)

        with patch("task.background.after_market.ohlcv_update_task.fdr.DataReader", return_value=df), \
             patch.object(task, "_update_stock_ohlcv", new=AsyncMock()) as mock_update:
            await task._backfill_historical_data(
                [("005930", "삼성전자", "KOSPI")], TARGET_DATE, False, time.time()
            )

        mock_stock_repo.upsert_ohlcv.assert_awaited_once()
        mock_update.assert_not_awaited()

    async def test_backfill_upserts_when_latest_db_date_differs(
        self, task, mock_stock_repo
    ):
        df = pd.DataFrame(
            [{"Open": 100, "High": 110, "Low": 90, "Close": 105, "Volume": 10}],
            index=[pd.to_datetime(TARGET_DATE)],
        )
        mock_stock_repo.upsert_ohlcv = AsyncMock()
        mock_stock_repo.get_stock_data = AsyncMock(
            return_value={"ohlcv": [{"date": "20260317", "close": 999}]}
        )

        with patch("task.background.after_market.ohlcv_update_task.fdr.DataReader", return_value=df), \
             patch.object(task, "_update_stock_ohlcv", new=AsyncMock()) as mock_update:
            await task._backfill_historical_data(
                [("005930", "삼성전자", "KOSPI")], TARGET_DATE, False, time.time()
            )

        mock_stock_repo.upsert_ohlcv.assert_awaited_once()
        mock_update.assert_not_awaited()

    async def test_update_stock_ohlcv_reraises_cancelled_error(self, task, mock_sqs):
        mock_sqs.get_ohlcv = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await task._update_stock_ohlcv("005930")

    async def test_save_bulk_to_db_with_progress_batches_records(self, task, mock_stock_repo):
        task.DB_UPSERT_BATCH_SIZE = 2
        mock_stock_repo.upsert_ohlcv = AsyncMock()
        records = [
            {"code": "005930", "date": "20260318"},
            {"code": "000660", "date": "20260318"},
            {"code": "035420", "date": "20260318"},
        ]

        with patch("task.background.after_market.ohlcv_update_task.asyncio.sleep", new=AsyncMock()):
            await task._save_bulk_to_db_with_progress(records, time.time())

        assert mock_stock_repo.upsert_ohlcv.await_count == 2
        assert task.get_progress()["processed"] == 3
        assert task.get_progress()["updated"] == 3

    async def test_verify_crawler_data_returns_false_on_api_failure(self, task, mock_sqs):
        mock_sqs.get_current_price = AsyncMock(return_value=None)

        result = await task._verify_crawler_data(pd.DataFrame(), "TEST")

        assert result is False

    async def test_verify_crawler_data_detects_ohlc_mismatch(self, task, mock_sqs):
        task.CANARY_STOCKS = ["005930"]
        mock_sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data={"output": {"stck_prpr": "70000", "stck_oprc": "69000", "stck_hgpr": "71000", "stck_lwpr": "68000"}},
            )
        )
        df = pd.DataFrame([
            {"Code": "005930", "Close": 70000, "Open": 69000, "High": 72000, "Low": 68000}
        ])

        result = await task._verify_crawler_data(df, "TEST")

        assert result is False

    async def test_verify_crawler_data_passes_with_object_output(self, task, mock_sqs):
        task.CANARY_STOCKS = ["005930"]
        mock_sqs.get_current_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="OK",
                data=SimpleNamespace(
                    stck_prpr="70000",
                    stck_oprc="69000",
                    stck_hgpr="71000",
                    stck_lwpr="68000",
                ),
            )
        )
        df = pd.DataFrame([
            {"Code": "005930", "Close": 70000, "Open": 69000, "High": 71000, "Low": 68000}
        ])

        result = await task._verify_crawler_data(df, "TEST")

        assert result is True
