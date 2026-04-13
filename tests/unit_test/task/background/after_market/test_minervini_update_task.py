import asyncio
import pytest
from datetime import datetime

from task.background.after_market.minervini_update_task import MinerviniUpdateTask


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
    def __init__(self):
        self.records = None

    async def upsert_daily_snapshot(self, trade_date, records):
        self.records = (trade_date, records)


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
