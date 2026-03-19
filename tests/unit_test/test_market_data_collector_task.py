"""
MarketDataCollectorTask 단위 테스트.
장 마감 후 전체 종목 현재가 수집 태스크 검증.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch
import pandas as pd

from task.background.market_data_collector_task import MarketDataCollectorTask
from managers.market_data_repository import MarketDataRepository
from interfaces.schedulable_task import TaskPriority, TaskState
from common.types import ResCommonResponse, ErrorCode


# --- Fixtures ---


@pytest.fixture
def mock_sqs():
    sqs = MagicMock()
    sqs.get_current_price = AsyncMock()
    return sqs


@pytest.fixture
def mock_mapper():
    """종목코드 매퍼 mock (3개 종목)."""
    mapper = MagicMock()
    mapper.df = pd.DataFrame([
        {"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"},
        {"종목코드": "000660", "종목명": "SK하이닉스", "시장구분": "KOSPI"},
        {"종목코드": "035420", "종목명": "NAVER", "시장구분": "KOSPI"},
    ])
    return mapper


@pytest.fixture
def mock_mapper_with_etf():
    """ETF/우선주 포함 매퍼 mock."""
    mapper = MagicMock()
    mapper.df = pd.DataFrame([
        {"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"},
        {"종목코드": "005935", "종목명": "삼성전자우", "시장구분": "KOSPI"},   # 우선주 (끝자리 5)
        {"종목코드": "069500", "종목명": "KODEX 200", "시장구분": "KOSPI"},    # ETF
        {"종목코드": "000660", "종목명": "SK하이닉스", "시장구분": "KOSPI"},
        {"종목코드": "999990", "종목명": "테스트스팩1호", "시장구분": "KOSDAQ"}, # 스팩
    ])
    return mapper


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test_market_data.db")
    r = MarketDataRepository(db_path=db_path)
    yield r
    r.close()


@pytest.fixture
def mock_mdm():
    mdm = MagicMock()
    mdm.is_market_open_now = AsyncMock(return_value=False)
    mdm.get_latest_trading_date = AsyncMock(return_value="20260318")
    return mdm


@pytest.fixture
def task(mock_sqs, mock_mapper, repo, mock_mdm):
    return MarketDataCollectorTask(
        stock_query_service=mock_sqs,
        stock_code_mapper=mock_mapper,
        repository=repo,
        market_date_manager=mock_mdm,
        logger=MagicMock(),
    )


def _make_price_response(code="005930", price=70000):
    """get_current_price 성공 응답 mock."""
    output = MagicMock()
    output.stck_prpr = str(price)
    output.stck_oprc = str(price - 1000)
    output.stck_hgpr = str(price + 1000)
    output.stck_lwpr = str(price - 1500)
    output.stck_sdpr = str(price - 500)
    output.prdy_vrss = "500"
    output.prdy_vrss_sign = "2"
    output.prdy_ctrt = "0.72"
    output.acml_vol = "10000000"
    output.acml_tr_pbmn = "700000000000"
    output.hts_avls = "4200000000000000"
    output.per = "12.5"
    output.pbr = "1.3"
    output.eps = "5600"
    output.w52_hgpr = "80000"
    output.w52_lwpr = "55000"

    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="현재가 조회 성공",
        data={"output": output},
    )


# --- 태스크 속성 테스트 ---


class TestTaskProperties:

    def test_task_name(self, task):
        assert task.task_name == "market_data_collector"

    def test_priority(self, task):
        assert task.priority == TaskPriority.LOW

    def test_initial_state(self, task):
        assert task.state == TaskState.IDLE

    def test_initial_progress(self, task):
        progress = task.get_progress()
        assert progress["running"] is False
        assert progress["processed"] == 0


# --- 수집 로직 테스트 ---


class TestCollectAllPrices:

    async def test_collect_stores_to_db(self, task, mock_sqs, repo):
        """수집 완료 후 DB에 데이터가 저장된다."""
        mock_sqs.get_current_price = AsyncMock(
            side_effect=lambda code: _make_price_response(code)
        )

        await task._collect_all_prices()

        result = repo.get_prices_by_date("20260318")
        assert len(result) == 3
        codes = {r["code"] for r in result}
        assert codes == {"005930", "000660", "035420"}

    async def test_collect_extracts_fields(self, task, mock_sqs, repo):
        """ResStockFullInfoApiOutput 필드가 정확히 추출된다."""
        mock_sqs.get_current_price = AsyncMock(
            return_value=_make_price_response("005930", 70000)
        )

        await task._collect_all_prices()

        result = repo.get_prices_by_date("20260318")
        assert len(result) == 3
        samsung = next(r for r in result if r["code"] == "005930")
        assert samsung["current_price"] == 70000
        assert samsung["open_price"] == 69000
        assert samsung["high_price"] == 71000
        assert samsung["per"] == 12.5
        assert samsung["market"] == "KOSPI"

    async def test_collect_skips_failed_responses(self, task, mock_sqs, repo):
        """API 실패 응답은 건너뛰고 성공한 종목만 저장한다."""
        call_count = 0

        async def _mock_get_current_price(code):
            nonlocal call_count
            call_count += 1
            if code == "000660":
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="조회 실패",
                    data=None,
                )
            return _make_price_response(code)

        mock_sqs.get_current_price = AsyncMock(side_effect=_mock_get_current_price)

        await task._collect_all_prices()

        result = repo.get_prices_by_date("20260318")
        codes = {r["code"] for r in result}
        assert "000660" not in codes
        assert "005930" in codes

    async def test_collect_updates_progress(self, task, mock_sqs):
        """수집 중 진행률이 업데이트된다."""
        mock_sqs.get_current_price = AsyncMock(
            side_effect=lambda code: _make_price_response(code)
        )

        await task._collect_all_prices()

        progress = task.get_progress()
        assert progress["running"] is False  # 완료 후
        assert progress["total"] == 3
        assert progress["processed"] == 3
        assert progress["collected"] == 3

    async def test_collect_skip_during_market_hours(self, task, mock_mdm, mock_sqs):
        """장 중에는 수집을 건너뛴다."""
        mock_mdm.is_market_open_now.return_value = True

        await task._collect_all_prices()

        mock_sqs.get_current_price.assert_not_called()

    async def test_collect_skip_already_collected(self, task, mock_sqs):
        """이미 수집한 날짜는 건너뛴다."""
        mock_sqs.get_current_price = AsyncMock(
            side_effect=lambda code: _make_price_response(code)
        )

        await task._collect_all_prices()  # 첫 번째 수집
        mock_sqs.get_current_price.reset_mock()

        await task._collect_all_prices()  # 동일 날짜 → 스킵
        mock_sqs.get_current_price.assert_not_called()

    async def test_collect_no_trading_date(self, task, mock_mdm, mock_sqs):
        """거래일을 확인할 수 없으면 중단."""
        mock_mdm.get_latest_trading_date.return_value = None

        await task._collect_all_prices()

        mock_sqs.get_current_price.assert_not_called()


# --- ETF/우선주 필터링 테스트 ---


class TestLoadAllStocks:

    def test_filters_etf_and_preferred(self, mock_sqs, mock_mapper_with_etf, repo, mock_mdm):
        """ETF, 우선주, 스팩이 필터링된다."""
        task = MarketDataCollectorTask(
            stock_query_service=mock_sqs,
            stock_code_mapper=mock_mapper_with_etf,
            repository=repo,
            market_date_manager=mock_mdm,
            logger=MagicMock(),
        )
        stocks = task._load_all_stocks()
        codes = [code for code, _, _ in stocks]
        assert "005930" in codes       # 삼성전자 ✓
        assert "000660" in codes       # SK하이닉스 ✓
        assert "005935" not in codes   # 우선주 ✗
        assert "069500" not in codes   # ETF ✗
        assert "999990" not in codes   # 스팩 ✗


# --- Suspend/Resume 테스트 ---


class TestSuspendResume:

    async def test_suspend_clears_event(self, task):
        """suspend 후 _suspend_event이 cleared 상태."""
        task._state = TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED
        assert not task._suspend_event.is_set()

    async def test_resume_sets_event(self, task):
        """resume 후 _suspend_event이 set 상태."""
        task._state = TaskState.RUNNING
        await task.suspend()
        await task.resume()
        assert task.state == TaskState.RUNNING
        assert task._suspend_event.is_set()

    async def test_suspend_pauses_collection(self, task, mock_sqs, repo):
        """suspend 시 chunk 사이에서 대기한다."""
        collected_codes = []
        barrier = asyncio.Event()

        original_get = _make_price_response

        async def _mock_get_current_price(code):
            collected_codes.append(code)
            if len(collected_codes) == 1:
                barrier.set()  # 첫 번째 chunk 완료 신호
            return original_get(code)

        mock_sqs.get_current_price = AsyncMock(side_effect=_mock_get_current_price)

        # chunk size를 1로 설정하여 종목 단위로 suspend 체크
        task.API_CHUNK_SIZE = 1
        task.CHUNK_SLEEP_SEC = 0

        collect_task = asyncio.create_task(task._collect_all_prices())

        await barrier.wait()  # 첫 번째 종목 수집 완료 대기

        # suspend → 이벤트 클리어
        task._state = TaskState.RUNNING
        await task.suspend()

        # 잠시 대기 후 수집이 멈추었는지 확인
        count_at_suspend = len(collected_codes)
        await asyncio.sleep(0.05)
        assert len(collected_codes) == count_at_suspend  # 더 이상 진행 안 됨

        # resume → 나머지 수집 완료
        await task.resume()
        await collect_task

        result = repo.get_prices_by_date("20260318")
        assert len(result) == 3


# --- Start/Stop 테스트 ---


class TestStartStop:

    async def test_start_creates_tasks(self, task):
        """start() 호출 시 asyncio 태스크가 생성된다."""
        await task.start()
        assert task.state == TaskState.RUNNING
        assert len(task._tasks) == 2  # collect + scheduler

        await task.stop()
        assert task.state == TaskState.STOPPED
        assert len(task._tasks) == 0

    async def test_start_idempotent(self, task):
        """이미 RUNNING이면 start()가 중복 실행되지 않는다."""
        await task.start()
        tasks_count = len(task._tasks)

        await task.start()  # 중복 호출
        assert len(task._tasks) == tasks_count  # 태스크 추가 안 됨

        await task.stop()


# --- _extract_record 테스트 ---


class TestExtractRecord:

    def test_extract_from_pydantic_model(self):
        """Pydantic 모델(ResStockFullInfoApiOutput)에서 필드 추출."""
        resp = _make_price_response("005930", 70000)

        record = MarketDataCollectorTask._extract_record(
            "005930", "삼성전자", "KOSPI", resp
        )

        assert record is not None
        assert record["code"] == "005930"
        assert record["current_price"] == 70000
        assert record["market"] == "KOSPI"

    def test_extract_from_dict(self):
        """dict 형태 output에서도 필드 추출."""
        resp = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="ok",
            data={
                "output": {
                    "stck_prpr": "70000",
                    "stck_oprc": "69000",
                    "stck_hgpr": "71000",
                    "stck_lwpr": "68500",
                    "stck_sdpr": "69500",
                    "prdy_vrss": "500",
                    "prdy_vrss_sign": "2",
                    "prdy_ctrt": "0.72",
                    "acml_vol": "10000000",
                    "acml_tr_pbmn": "700000000000",
                    "hts_avls": "4200000000000000",
                    "per": "12.5",
                    "pbr": "1.3",
                    "eps": "5600",
                    "w52_hgpr": "80000",
                    "w52_lwpr": "55000",
                }
            },
        )

        record = MarketDataCollectorTask._extract_record(
            "005930", "삼성전자", "KOSPI", resp
        )

        assert record is not None
        assert record["current_price"] == 70000
        assert record["per"] == 12.5

    def test_extract_none_response(self):
        """None 응답은 None 반환."""
        assert MarketDataCollectorTask._extract_record("005930", "삼성전자", "KOSPI", None) is None

    def test_extract_empty_data(self):
        """data가 None인 응답은 None 반환."""
        resp = ResCommonResponse(rt_cd="0", msg1="ok", data=None)
        assert MarketDataCollectorTask._extract_record("005930", "삼성전자", "KOSPI", resp) is None
