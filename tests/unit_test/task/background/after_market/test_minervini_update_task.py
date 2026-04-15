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

    try:
        await task.start()
        assert task.state == TaskState.RUNNING

        await task.suspend()
        assert task.state == TaskState.SUSPENDED

        await task.resume()
        assert task.state == TaskState.RUNNING

        prog = task.get_progress()
        assert "running" in prog
        assert "last_updated" in prog
    finally:
        await task.stop()  # 백그라운드 스케줄러 Task 취소 — 미취소 시 12시간 sleep hang


@pytest.mark.asyncio
async def test_on_market_closed_parses_yyyymmdd_format():
    """_on_market_closed가 'YYYYMMDD' 형식의 날짜를 올바르게 파싱하는지 확인 (버그 픽스 검증)"""
    mcs = DummyMCS(is_open=False, latest_date="20260415")
    stock_repo = DummyStockRepo()
    sc_repo = DummyStockCodeRepo([])

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
    )

    # '20260415' 형식을 파싱할 때 ValueError가 발생하지 않아야 함
    await task._on_market_closed("20260415")


@pytest.mark.asyncio
async def test_on_market_closed_triggers_refresh_when_not_updated():
    """_on_market_closed: 오늘 미업데이트 상태면 refresh가 호출되는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20260415")
    stock_repo = DummyStockRepo()
    sc_repo = DummyStockCodeRepo([])

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
    )
    task._updated_at = None  # 업데이트 이력 없음

    refresh_called = []

    async def mock_refresh(force=False):
        refresh_called.append(True)

    task.refresh_minervini_stage2 = mock_refresh

    await task._on_market_closed("20260415")

    assert refresh_called, "업데이트 이력이 없으면 refresh가 호출되어야 함"


@pytest.mark.asyncio
async def test_on_market_closed_skips_refresh_when_already_updated_today():
    """_on_market_closed: 이미 당일 업데이트를 마쳤다면 refresh를 건너뛰는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20260415")
    stock_repo = DummyStockRepo()
    sc_repo = DummyStockCodeRepo([])

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
    )
    task._updated_at = datetime(2026, 4, 15, 16, 0, 0)  # 당일 업데이트 완료

    refresh_called = []

    async def mock_refresh(force=False):
        refresh_called.append(True)

    task.refresh_minervini_stage2 = mock_refresh

    await task._on_market_closed("20260415")

    assert not refresh_called, "당일 업데이트가 완료된 경우 refresh를 건너뛰어야 함"


@pytest.mark.asyncio
async def test_on_market_closed_triggers_refresh_when_updated_on_different_date():
    """_on_market_closed: 마지막 업데이트가 다른 날짜면 refresh가 호출되는지 확인"""
    mcs = DummyMCS(is_open=False, latest_date="20260415")
    stock_repo = DummyStockRepo()
    sc_repo = DummyStockCodeRepo([])

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
    )
    task._updated_at = datetime(2026, 4, 14, 16, 0, 0)  # 전날 업데이트

    refresh_called = []

    async def mock_refresh(force=False):
        refresh_called.append(True)

    task.refresh_minervini_stage2 = mock_refresh

    await task._on_market_closed("20260415")

    assert refresh_called, "마지막 업데이트가 다른 날짜면 refresh가 호출되어야 함"


# ── 추가 TC: 미커버 라인 보강 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_name_and_scheduler_label():
    """task_name / _scheduler_label 프로퍼티 반환값 확인 (L81, L85)"""
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    assert task.task_name == "minervini_update"
    assert task._scheduler_label == "MinerviniUpdateTask"


@pytest.mark.asyncio
async def test_load_all_stocks_skips_empty_code():
    """종목코드가 빈 행은 _load_all_stocks에서 건너뛰는지 확인 (L119)"""
    rows = [
        {"종목코드": "", "종목명": "빈코드", "시장구분": "KOSPI"},
        {"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"},
    ]
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=DummyStockCodeRepo(rows),
    )
    stocks = task._load_all_stocks()
    assert len(stocks) == 1
    assert stocks[0][0] == "0001"


@pytest.mark.asyncio
async def test_refresh_skips_when_already_refreshing():
    """_is_refreshing=True 이면 refresh 즉시 리턴 (L130-L131)"""
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=DummyStockCodeRepo([]),
        stock_repository=DummyStockRepo(),
    )
    task._is_refreshing = True
    await task.refresh_minervini_stage2(force=True)
    # refreshing 플래그가 여전히 True 이고 DB 미기록
    assert task._is_refreshing is True
    assert task._stock_repo.records is None


@pytest.mark.asyncio
async def test_no_dpc_warning_when_price_data_missing():
    """가격 데이터 부족 + DPC 미설정 시 warning 경고만 하고 계속 진행 (L163)"""
    mcs = DummyMCS(is_open=False, latest_date="20250101")
    stock_repo = DummyStockRepo(price_count=10)  # < MIN_PRICE_COUNT
    sc_repo = DummyStockCodeRepo([])

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        market_calendar_service=mcs,
        daily_price_collector_task=None,  # DPC 미설정
    )
    # 예외 없이 완료되어야 함
    await task.refresh_minervini_stage2(force=True)
    assert task._updated_at is not None


@pytest.mark.asyncio
async def test_stage_resp_non_numeric_fallback():
    """get_stage_for_code가 int() 변환 불가 값을 반환하면 stage=None으로 복구 (L232-L233)"""
    sc_repo = DummyStockCodeRepo([{"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"}])

    class BadValueMinervini:
        async def get_stage_for_code(self, code):
            return "invalid_string"  # int("invalid_string") → ValueError → L232-233

    task = MinerviniUpdateTask(
        minervini_service=BadValueMinervini(),
        stock_code_repository=sc_repo,
        stock_repository=DummyStockRepo(),
    )
    await task.refresh_minervini_stage2(force=True)
    _, records = task._stock_repo.records
    assert records[0]["minervini_stage"] == 0


@pytest.mark.asyncio
async def test_refresh_without_sqs_broker_rs():
    """sqs/broker/rs_svc 미설정 시 stage2 종목에 대해 asyncio.sleep fallback 경로 실행 (L250, L261, L265)"""
    rows = [{"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"}]
    sc_repo = DummyStockCodeRepo(rows)
    minervini = DummyMinerviniSvc({"0001": (2, "r")})
    stock_repo = DummyStockRepo()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        stock_query_service=None,   # L250
        broker_api_wrapper=None,    # L261
        rs_rating_service=None,     # L265
    )
    await task.refresh_minervini_stage2(force=True)

    cache = await task.get_minervini_stage2_cache()
    assert len(cache) == 1
    assert cache[0]["code"] == "0001"


@pytest.mark.asyncio
async def test_rs_rating_from_data_rs_rating_attr():
    """rs_resp.data.rs_rating 속성 경로로 rs_rating 수집 (L287-L290)"""
    rows = [{"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"}]
    sc_repo = DummyStockCodeRepo(rows)
    minervini = DummyMinerviniSvc({"0001": (2, "r")})
    stock_repo = DummyStockRepo()

    class RatingData:
        rs_rating = 77

    class RatingResp:
        data = RatingData()

    class DummyRSDataAttr:
        async def get_rating(self, _code):
            return RatingResp()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        rs_rating_service=DummyRSDataAttr(),
    )
    await task.refresh_minervini_stage2(force=True)

    _, records = stock_repo.records
    record_map = {r["code"]: r for r in records}
    assert record_map["0001"]["rs_rating"] == 77


@pytest.mark.asyncio
async def test_rs_rating_from_data_numeric():
    """rs_resp.data 가 int/float 일 때 rs_rating 수집 (L287-L288, L291)"""
    rows = [{"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"}]
    sc_repo = DummyStockCodeRepo(rows)
    minervini = DummyMinerviniSvc({"0001": (2, "r")})
    stock_repo = DummyStockRepo()

    class NumericDataResp:
        data = 55.0  # isinstance(d, (int, float)) → True

    class DummyRSNumericData:
        async def get_rating(self, code):
            return NumericDataResp()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        rs_rating_service=DummyRSNumericData(),
    )
    await task.refresh_minervini_stage2(force=True)

    _, records = stock_repo.records
    record_map = {r["code"]: r for r in records}
    assert record_map["0001"]["rs_rating"] == 55


@pytest.mark.asyncio
async def test_rs_rating_valueerror_fallback():
    """rs_rating 계산 중 ValueError 발생 시 0으로 폴백 (L294-L295)"""
    rows = [{"종목코드": "0001", "종목명": "A", "시장구분": "KOSPI"}]
    sc_repo = DummyStockCodeRepo(rows)
    minervini = DummyMinerviniSvc({"0001": (2, "r")})
    stock_repo = DummyStockRepo()

    class BadRatingData:
        rs_rating = "not_a_number"  # int("not_a_number") → ValueError

    class BadRatingResp:
        data = BadRatingData()

    class DummyRSBadValue:
        async def get_rating(self, code):
            return BadRatingResp()

    task = MinerviniUpdateTask(
        minervini_service=minervini,
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        rs_rating_service=DummyRSBadValue(),
    )
    await task.refresh_minervini_stage2(force=True)

    # 인메모리 캐시에서 확인: except절이 val=0으로 복구했는지
    # (DB 저장 시 "0 or None → None" 변환이 있어 캐시로 검증)
    cache = task._minervini_stage2_cache
    assert len(cache) == 1
    assert cache[0].get("rs_rating") == 0


@pytest.mark.asyncio
async def test_notification_emitted_on_completion():
    """갱신 완료 후 notification_service.emit이 호출되는지 확인 (L334)"""
    class DummyNotificationService:
        def __init__(self):
            self.emitted = []

        async def emit(self, category, level, title, message):
            self.emitted.append((title, message))

    sc_repo = DummyStockCodeRepo([])
    stock_repo = DummyStockRepo()
    notif = DummyNotificationService()

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        notification_service=notif,
    )
    await task.refresh_minervini_stage2(force=True)

    assert len(notif.emitted) == 1
    title, _ = notif.emitted[0]
    assert "S2" in title or "Minervini" in title


@pytest.mark.asyncio
async def test_telegram_exception_is_swallowed():
    """telegram reporter가 예외를 던져도 refresh가 정상 완료되는지 확인 (L343-L344)"""
    class FailingTelegram:
        async def send_minervini_report(self, collected, report_date):
            raise RuntimeError("Telegram connection failed")

    sc_repo = DummyStockCodeRepo([])
    stock_repo = DummyStockRepo()

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
        telegram_reporter=FailingTelegram(),
    )
    # 예외 전파 없이 완료되어야 함
    await task.refresh_minervini_stage2(force=True)
    assert task._updated_at is not None
    assert task._is_refreshing is False


@pytest.mark.asyncio
async def test_db_write_exception_is_swallowed():
    """DB 쓰기 실패 시 예외가 삼켜지고 refresh가 정상 완료되는지 확인 (L363-L364)"""
    class FailingStockRepo(DummyStockRepo):
        async def update_minervini_fields(self, trade_date, records):
            raise Exception("DB write failed")

    sc_repo = DummyStockCodeRepo([])
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=FailingStockRepo(),
    )
    await task.refresh_minervini_stage2(force=True)
    assert task._updated_at is not None
    assert task._is_refreshing is False


@pytest.mark.asyncio
async def test_outer_exception_handled_with_notification():
    """내부 예외 발생 시 error 로그 + notification 에러 emit (L366-L369)"""
    class DummyNotificationService:
        def __init__(self):
            self.emitted = []

        async def emit(self, category, level, title, message):
            self.emitted.append((level, title))

    notif = DummyNotificationService()
    # stock_code_repository=None → _load_all_stocks에서 AttributeError 발생
    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=None,
        notification_service=notif,
    )
    await task.refresh_minervini_stage2(force=True)

    # 에러 emit이 호출되어야 함
    assert len(notif.emitted) == 1
    level, _ = notif.emitted[0]
    from services.notification_service import NotificationLevel
    assert level == NotificationLevel.ERROR
    assert task._is_refreshing is False


@pytest.mark.asyncio
async def test_get_cache_creates_background_task_when_empty():
    """캐시 비어있고 갱신 중 아닐 때 asyncio.create_task로 background 갱신 예약 (L380-L382)"""
    from unittest.mock import patch, MagicMock

    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    assert task._minervini_stage2_cache == []
    assert task._is_refreshing is False

    mock_task = MagicMock()
    with patch("task.background.after_market.minervini_update_task.asyncio.create_task", return_value=mock_task) as mock_ct:
        result = await task.get_minervini_stage2_cache()

    assert mock_ct.called
    assert result == []


@pytest.mark.asyncio
async def test_force_collect_invokes_refresh():
    """force_collect 호출 시 refresh_minervini_stage2(force=True)가 실행되는지 확인 (L389-L394)"""
    sc_repo = DummyStockCodeRepo([])
    stock_repo = DummyStockRepo()

    task = MinerviniUpdateTask(
        minervini_service=DummyMinerviniSvc({}),
        stock_code_repository=sc_repo,
        stock_repository=stock_repo,
    )
    await task.force_collect()

    assert task._updated_at is not None
    assert task._is_refreshing is False


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
