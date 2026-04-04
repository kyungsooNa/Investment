import pytest
import asyncio
import pandas as pd
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
from common.types import ErrorCode, ResCommonResponse

@pytest.fixture
def mock_sqs():
    return AsyncMock()

@pytest.fixture
def mock_scr():
    scr = MagicMock()
    # _load_all_stocks() 필터링 동작 검증을 위해 다양한 케이스 포함
    scr.df = pd.DataFrame({
        "종목코드": ["005930", "000660", "069500", "123456"], 
        "종목명": ["삼성전자", "SK하이닉스", "KODEX 200", "이상한스팩"],
        "시장구분": ["KOSPI", "KOSPI", "KOSPI", "KOSDAQ"]
    })
    return scr

@pytest.fixture
def mock_sr():
    return AsyncMock()

@pytest.fixture
def mock_mcs():
    mcs = AsyncMock()
    mcs.is_market_open_now.return_value = False
    mcs.get_latest_trading_date.return_value = "2025-01-01"
    return mcs

@pytest.fixture
def mock_ns():
    return AsyncMock()

@pytest.fixture
def task(mock_sqs, mock_scr, mock_sr, mock_mcs, mock_ns):
    t = DailyPriceCollectorTask(
        stock_query_service=mock_sqs,
        stock_code_repository=mock_scr,
        stock_repo=mock_sr,
        market_calendar_service=mock_mcs,
        logger=MagicMock(),
        notification_service=mock_ns
    )
    # 검증을 빠르게 하기 위해 카나리 종목 단순화
    t.CANARY_STOCKS = ["005930"]
    return t

@pytest.mark.asyncio
async def test_collect_all_prices_market_open(task, mock_mcs):
    """장 중일 때 수집을 건너뛰는지 확인"""
    mock_mcs.is_market_open_now.return_value = True
    with patch.object(task, '_try_collect_via_fdr', new_callable=AsyncMock) as mock_fdr:
        await task._collect_all_prices()
        mock_fdr.assert_not_called()

@pytest.mark.asyncio
async def test_collect_all_prices_already_collected(task):
    """이미 해당 날짜의 수집이 완료되었을 때 건너뛰는지 확인"""
    task._last_collected_date = "2025-01-01"
    with patch.object(task, '_try_collect_via_fdr', new_callable=AsyncMock) as mock_fdr:
        await task._collect_all_prices()
        mock_fdr.assert_not_called()

@pytest.mark.asyncio
async def test_collect_all_prices_tier1_fdr_success(task):
    """Tier 1 (FDR) 수집 성공 시 Broker API 호출 안 하는지 확인"""
    with patch.object(task, '_try_collect_via_fdr', return_value=True) as mock_fdr, \
         patch.object(task, '_finish_collection', new_callable=AsyncMock) as mock_finish, \
         patch.object(task, '_collect_via_broker_api', new_callable=AsyncMock) as mock_api:
        
        await task._collect_all_prices()
        mock_fdr.assert_called_once()
        mock_api.assert_not_called()
        mock_finish.assert_called_once()
        args, _ = mock_finish.call_args
        assert args[0] == "2025-01-01"
        assert args[2] == "FDR"

@pytest.mark.asyncio
async def test_collect_all_prices_tier2_broker_api_success(task):
    """크롤링(FDR) 실패 시 최후 수단으로 Broker API 수집 폴백 동작 확인"""
    with patch.object(task, '_try_collect_via_fdr', return_value=False), \
         patch.object(task, '_collect_via_broker_api', new_callable=AsyncMock) as mock_api, \
         patch.object(task, '_finish_collection', new_callable=AsyncMock) as mock_finish:
        
        await task._collect_all_prices()
        mock_api.assert_called_once()
        mock_finish.assert_called_once()
        args, _ = mock_finish.call_args
        assert args[2] == "Broker API"

@pytest.mark.asyncio
async def test_collect_all_prices_caching_and_cleanup(task):
    """_load_all_stocks 호출 캐싱 및 파이프라인 종료 후 캐시 초기화 검증"""
    with patch.object(task, '_load_all_stocks', return_value=[("005930", "삼성전자", "KOSPI")]) as mock_load, \
         patch.object(task, '_try_collect_via_fdr', return_value=True), \
         patch.object(task, '_finish_collection', new_callable=AsyncMock):
        
        assert getattr(task, "_all_stocks_cache", None) is None
        await task._collect_all_prices()
        
        mock_load.assert_called_once()
        assert task._all_stocks_cache is None

@pytest.mark.asyncio
async def test_verify_crawler_data_success(task):
    """API 응답과 크롤링 데이터가 일치할 때 검증 성공 확인"""
    df_crawled = pd.DataFrame({
        "종목코드": ["005930"],
        "종가": [80000],
        "시가": [79000],
        "고가": [81000],
        "저가": [78000]
    }).set_index("종목코드")

    api_resp = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="",
        data={"output": {
            "stck_prpr": "80000",
            "stck_oprc": "79000",
            "stck_hgpr": "81000",
            "stck_lwpr": "78000"
        }}
    )
    with patch.object(task, '_fetch_with_retry', return_value=api_resp):
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is True

@pytest.mark.asyncio
async def test_verify_crawler_data_fail_mismatch(task):
    """API 응답과 크롤링 데이터가 하나라도 불일치할 때 검증 실패 확인"""
    df_crawled = pd.DataFrame({
        "종목코드": ["005930"],
        "종가": [80000],
        "시가": [79000],
        "고가": [81000],
        "저가": [78000]
    }).set_index("종목코드")

    api_resp = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="",
        data={"output": {
            "stck_prpr": "81000", # 종가 불일치
            "stck_oprc": "79000",
            "stck_hgpr": "81000",
            "stck_lwpr": "78000"
        }}
    )
    with patch.object(task, '_fetch_with_retry', return_value=api_resp):
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is False

@pytest.mark.asyncio
async def test_verify_crawler_data_api_fail(task):
    """검증용 API 호출 자체가 실패했을 때 검증 실패 처리 확인"""
    df_crawled = pd.DataFrame()
    with patch.object(task, '_fetch_with_retry', return_value=None):
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is False

@pytest.mark.asyncio
async def test_try_collect_via_fdr(task):
    """FDR 방식 수집 스레드 실행, 검증, 변환 및 DB Batch 저장 확인"""
    df_fdr = pd.DataFrame({"Code": ["005930"], "Close": [80000]})
    
    with patch('asyncio.to_thread', new_callable=AsyncMock, return_value=df_fdr), \
         patch.object(task, '_verify_crawler_data', return_value=True), \
         patch.object(task, '_format_dataframe_to_records', return_value=[{"code": "005930"}]), \
         patch.object(task, '_save_bulk_to_db_with_progress', new_callable=AsyncMock) as mock_save:
        
        result = await task._try_collect_via_fdr("2025-01-01", 0.0)
        assert result is True
        mock_save.assert_called_once()
        assert task._progress["status"] == "FinanceDataReader 일괄 수집 중..."

def test_format_dataframe_to_records(task):
    """DataFrame 파싱 로직 및 ETF/스팩 등 제외 필터 정상 작동 확인"""
    df = pd.DataFrame({
        "종목코드": ["005930", "069500", "000000", "000660"], 
        "종가": [80000, 30000, 0, 150000],
        "시가": [79000, 29000, 0, 140000],
        "고가": [81000, 31000, 0, 160000],
        "저가": [78000, 28000, 0, 130000],
        "거래량": [1000, 500, 0, 2000],
        "거래대금": [80000000, 15000000, 0, 300000000],
        "시가총액": [400000000000, 10000000000, 0, 100000000000],
        "대비": [1000, 1000, 0, -2000],
        "등락률": [1.25, 3.45, 0, -1.31]
    })

    records = task._format_dataframe_to_records(df)
    
    # KODEX 200 (069500) 및 000000 등은 제외되어 2개만 남아야 함
    assert len(records) == 2
    
    r_5930 = next(r for r in records if r["code"] == "005930")
    assert r_5930["current_price"] == 80000
    assert r_5930["change_price"] == 1000
    assert r_5930["change_sign"] == "2" # 상승 (대비값 양수)

    r_0660 = next(r for r in records if r["code"] == "000660")
    assert r_0660["change_price"] == -2000
    assert r_0660["change_sign"] == "5" # 하락 (대비값 음수)

@pytest.mark.asyncio
async def test_save_bulk_to_db_with_progress(task):
    """DB 일괄 저장 시 배치 단위로 쪼개어 호출하는지 확인"""
    task.DB_UPSERT_BATCH_SIZE = 2
    records = [{"code": "1"}, {"code": "2"}, {"code": "3"}]
    
    await task._save_bulk_to_db_with_progress("2025-01-01", records, 0.0)
    
    # 총 3개, batch_size=2 이므로 2번 호출되어야 함
    assert task._stock_repo.upsert_daily_snapshot.call_count == 2
    assert task._progress["processed"] == 3
    assert task._progress["status"] == "DB 저장 중..."

@pytest.mark.asyncio
async def test_finish_collection(task):
    """종료 처리 로직 및 알림 발생 확인"""
    await task._finish_collection("2025-01-01", 0.0, "TEST")
    assert task._last_collected_date == "2025-01-01"
    task._ns.emit.assert_called_once()

@pytest.mark.asyncio
async def test_collect_via_broker_api(task):
    """Broker API 청크 기반 수집 로직 확인"""
    task.API_CHUNK_SIZE = 2
    task.DB_UPSERT_BATCH_SIZE = 2
    
    with patch.object(task, '_load_all_stocks', return_value=[("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI")]), \
         patch.object(task, '_fetch_with_retry', new_callable=AsyncMock) as mock_fetch:
        
        mock_fetch.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="",
            data={"output": {"stck_prpr": "80000", "acml_vol": "1000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000", "stck_sdpr": "79000", "prdy_vrss": "1000", "prdy_vrss_sign": "2", "prdy_ctrt": "1.25", "acml_tr_pbmn": "80000000", "hts_avls": "400000000000", "per": "10.5", "pbr": "1.5", "eps": "100", "w52_hgpr": "90000", "w52_lwpr": "70000"}}
        )
        
        await task._collect_via_broker_api("2025-01-01", 0.0)
        
        # 2개 종목을 API로 호출하므로 _fetch_with_retry는 2번 호출됨
        assert mock_fetch.call_count == 2
        # batch_size=2 이므로 2개가 채워지거나 루프 종료 시 1번 upsert 호출됨
        task._stock_repo.upsert_daily_snapshot.assert_called_once()
        assert task._progress["status"] == "증권사 API 수집 중 (Fallback)..."