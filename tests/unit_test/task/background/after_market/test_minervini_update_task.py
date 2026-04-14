import asyncio
import pytest
from datetime import datetime

from task.background.after_market.minervini_update_task import MinerviniUpdateTask
from interfaces.schedulable_task import TaskState


class DummyStockCodeRepo:
    def __init__(self, rows):
        import pandas as pd
        self.df = pd.DataFrame(rows)


class DummyMinerviniSvc:
    def __init__(self, mapping):
        # mapping: code -> stage or (stage, reason)
        self._mapping = mapping

    async def get_stage_for_code(self, code):
        # simulate async call
        await asyncio.sleep(0)
        return self._mapping.get(code, 0)


class DummySQS:
    async def get_current_price(self, code, caller=None):
        class R:
            def __init__(self, data):
                self.data = data

        await asyncio.sleep(0)
        return R({"stck_prpr": 100, "prdy_ctrt": 1.5, "prdy_vrss": 2})


class DummyBroker:
    async def get_market_cap(self, code):
        class R:
            def __init__(self, data):
                self.data = data

        await asyncio.sleep(0)
        return R(1_000_000)


class DummyRS:
    async def get_rating(self, code):
        await asyncio.sleep(0)
        return 85


class DummyStockRepo:
    def __init__(self, price_count=9999, stage_count=0, stage2_db_data=None):
        self.records = None
        self.price_count = price_count
        self.stage_count = stage_count
        self.stage2_db_data = stage2_db_data or []

    async def update_minervini_fields(self, trade_date, records):
        self.records = (trade_date, records)

    async def get_count_by_date(self, trade_date):
        return self.price_count

    async def get_minervini_stage_count(self, trade_date):
        return self.stage_count

    async def get_minervini_stage2_stocks(self, trade_date):
        return self.stage2_db_data


class DummyMCS:
    def __init__(self, is_open=False, latest_date="20250101"):
        self._is_open = is_open
        self._latest_date = latest_date
    async def is_market_open_now(self):
        return self._is_open
    async def get_latest_trading_date(self):
        return self._latest_date


class DummyDailyPriceCollector:
    def __init__(self):
        self.force_collect_called = False
        self._is_collecting = False
        self._collection_done_event = asyncio.Event()
        self._collection_done_event.set()
    async def force_collect(self):
        self.force_collect_called = True


class DummyTelegram:
    def __init__(self):
        self.sent = None

    async def send_minervini_report(self, collected, report_date):
        self.sent = (collected, report_date)


@pytest.mark.asyncio
async def test_persist_all_stages(tmp_path):
    # prepare a small stock list with two codes
    rows = [
        {"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"},
        {"종목코드": "0002", "종목명": "B", "시장구분": "KOSDAQ"},
    ]
    sc_repo = DummyStockCodeRepo(rows)

    # minervini: code 0001 -> stage 1, 0002 -> stage 2
    minervini = DummyMinerviniSvc({"0001": 1, "0002": (2, "reason")})

    sqs = DummySQS()
    broker = DummyBroker()
    rs = DummyRS()
    stock_repo = DummyStockRepo()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        stock_query_service=sqs,
        broker_api_wrapper=broker,
        rs_rating_service=rs,
        market_clock=None,
        logger=None,
    )

    await task.refresh_minervini_stage2(force=True)

    # assert stock_repo.upsert_daily_snapshot was called and contains both codes
    assert stock_repo.records is not None
    trade_date, records = stock_repo.records
    codes = {r["code"] for r in records}
    assert codes == {"0001", "0002"}

    # confirm stages persisted correctly
    stage_map = {r["code"]: r["minervini_stage"] for r in records}
    assert stage_map["0001"] == 1
    assert stage_map["0002"] == 2


@pytest.mark.asyncio
async def test_telegram_only_stage2():
    rows = [
        {"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"},
        {"종목코드": "0002", "종목명": "B", "시장구분": "KOSDAQ"},
        {"종목코드": "0003", "종목명": "C", "시장구분": "KOSPI"},
    ]
    sc_repo = DummyStockCodeRepo(rows)
    # minervini: only 0002 is stage2
    minervini = DummyMinerviniSvc({"0001": 0, "0002": (2, "r"), "0003": 1})

    sqs = DummySQS()
    broker = DummyBroker()
    rs = DummyRS()
    stock_repo = DummyStockRepo()
    tg = DummyTelegram()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        stock_query_service=sqs,
        broker_api_wrapper=broker,
        rs_rating_service=rs,
        market_clock=None,
        logger=None,
        telegram_reporter=tg,
    )

    await task.refresh_minervini_stage2(force=True)

    # telegram should have been sent
    assert tg.sent is not None
    collected, report_date = tg.sent
    # only stage2 code should be in collected
    codes = {it.get("code") for it in collected}
    assert codes == {"0002"}


@pytest.mark.asyncio
async def test_rs_rating_persisted_in_db_records():
    """refresh_minervini_stage2 실행 후 DB upsert 레코드에 rs_rating이 포함된다."""
    rows = [
        {"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"},
        {"종목코드": "0002", "종목명": "B", "시장구분": "KOSDAQ"},
    ]
    sc_repo = DummyStockCodeRepo(rows)
    # 0002 만 Stage2
    minervini = DummyMinerviniSvc({"0001": 1, "0002": (2, "reason")})
    sqs = DummySQS()
    broker = DummyBroker()
    rs = DummyRS()      # get_rating returns 85
    stock_repo = DummyStockRepo()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        stock_query_service=sqs,
        broker_api_wrapper=broker,
        rs_rating_service=rs,
        market_clock=None,
        logger=None,
    )

    await task.refresh_minervini_stage2(force=True)

    assert stock_repo.records is not None
    _, records = stock_repo.records
    record_map = {r["code"]: r for r in records}

    # Stage2 종목에는 rs_rating=85 가 포함되어야 함
    assert record_map["0002"]["rs_rating"] == 85

    # Stage1 종목도 rs_rating 키를 가지고 있어야 함 (None 허용)
    assert "rs_rating" in record_map["0001"]


@pytest.mark.asyncio
async def test_skip_when_market_open():
    """장이 열려 있을 때는 수집을 건너뛰는지 확인"""
    mcs = DummyMCS(is_open=True)
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}), market_calendar_service=mcs)
    await task.refresh_minervini_stage2()
    
    # 진행 중 상태가 아님을 확인
    assert task._is_refreshing is False
    assert task._progress["status"] == ""


@pytest.mark.asyncio
async def test_skip_already_updated_today():
    """이미 오늘 업데이트를 마쳤다면 건너뛰는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20250101")
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}), market_calendar_service=mcs)
    task._updated_at = datetime(2025, 1, 1, 15, 0, 0)
    
    await task.refresh_minervini_stage2(force=False)
    
    # 스킵되었으므로 _progress의 status가 초기화 상태 그대로임
    assert task._progress["status"] == ""


@pytest.mark.asyncio
async def test_dpc_trigger_when_data_missing():
    """가격 데이터가 기준치(MIN_PRICE_COUNT) 미만일 때 DailyPriceCollector를 트리거하는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20250101")
    dpc = DummyDailyPriceCollector()
    # 가격 데이터가 100개밖에 없다고 가정 (< 500)
    stock_repo = DummyStockRepo(price_count=100)
    
    sc_repo = DummyStockCodeRepo([])
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
        daily_price_collector_task=dpc
    )
    
    await task.refresh_minervini_stage2(force=True)
    assert dpc.force_collect_called is True


@pytest.mark.asyncio
async def test_dpc_wait_when_collecting():
    """DailyPriceCollector가 이미 돌고 있다면 대기(wait)하는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20250101")
    dpc = DummyDailyPriceCollector()
    dpc._is_collecting = True
    dpc._collection_done_event.clear()
    
    stock_repo = DummyStockRepo(price_count=100)
    sc_repo = DummyStockCodeRepo([])
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
        daily_price_collector_task=dpc
    )
    
    # 백그라운드에서 이벤트를 해제하여 대기를 끝냄
    async def release_event():
        await asyncio.sleep(0.1)
        dpc._collection_done_event.set()
        
    asyncio.create_task(release_event())
    await task.refresh_minervini_stage2(force=True)
    
    # 대기를 했으므로 force_collect를 직접 호출하지 않음
    assert dpc.force_collect_called is False


@pytest.mark.asyncio
async def test_load_from_db_when_stage_data_exists():
    """DB에 Stage 데이터가 이미 존재한다면, API를 타지 않고 DB에서 바로 로드하는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20250101")
    db_data = [{"code": "005930", "name": "Samsung", "minervini_stage": 2, "rs_rating": 90}]
    # Stage 카운트가 10개라고 가정
    stock_repo = DummyStockRepo(stage_count=10, stage2_db_data=db_data)
    
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_repository=stock_repo,
        market_calendar_service=mcs
    )
    
    await task.refresh_minervini_stage2(force=False)
    
    cache = await task.get_minervini_stage2_cache()
    assert len(cache) == 1
    assert cache[0]["code"] == "005930"
    assert cache[0]["stage"] == 2


@pytest.mark.asyncio
async def test_lifecycle_and_progress():
    """태스크의 상태 변경 라이프사이클 및 get_progress 기능 검증"""
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    
    await task.start()
    assert task.state == TaskState.RUNNING
    
    await task.suspend()
    assert task.state == TaskState.SUSPENDED
    
    await task.resume()
    assert task.state == TaskState.RUNNING
    
    prog = task.get_progress()
    assert "running" in prog
    assert "last_updated" in prog


@pytest.mark.asyncio
async def test_exception_in_stage_retrieval():
    """API 응답 등에서 예외가 발생했을 때 앱이 뻗지 않고 Stage=0으로 복구하는지 확인"""
    sc_repo = DummyStockCodeRepo([{"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"}])
    
    class BadMinervini:
        async def get_stage_for_code(self, code):
            raise Exception("API Fail")
            
    task = MinerviniUpdateTask(
        minervini_service=BadMinervini(), 
        stock_code_repository=sc_repo, 
        stock_repository=DummyStockRepo()
    )
    
    await task.refresh_minervini_stage2(force=True)
    
    assert task._stock_repo.records is not None
    _, records = task._stock_repo.records
    # 예외가 발생한 종목은 Stage 0으로 취급되어야 함
    assert records[0]["minervini_stage"] == 0
