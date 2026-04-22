import pytest
import asyncio
import pandas as pd
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

from interfaces.schedulable_task import TaskState
from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask, _chunked
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
    # 다중 카나리 종목 검증 로직 테스트를 위해 2개 지정
    t.CANARY_STOCKS = ["005930", "000660"]
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
async def test_collect_all_prices_broker_api_direct(task):
    """FDR 제거 후 _collect_all_prices 는 증권사 API를 직접 호출한다."""
    with patch.object(task, '_collect_via_broker_api', new_callable=AsyncMock) as mock_api, \
         patch.object(task, '_finish_collection', new_callable=AsyncMock) as mock_finish:

        await task._collect_all_prices()
        mock_api.assert_called_once()
        mock_finish.assert_called_once()
        args, _ = mock_finish.call_args
        assert args[0] == "2025-01-01"
        assert args[2] == "Broker API"

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
    """API 응답과 크롤링 데이터가 모두 일치할 때 검증 성공 확인"""
    df_crawled = pd.DataFrame({
        "종목코드": ["005930", "000660"],
        "종가": [80000, 150000],
        "시가": [79000, 140000],
        "고가": [81000, 160000],
        "저가": [78000, 130000]
    }).set_index("종목코드")

    async def mock_fetch(code, force_fresh=False):
        data = {
            "005930": {"stck_prpr": "80000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000"},
            "000660": {"stck_prpr": "150000", "stck_oprc": "140000", "stck_hgpr": "160000", "stck_lwpr": "130000"}
        }
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={"output": data[code]})

    with patch.object(task, '_fetch_with_retry', side_effect=mock_fetch):
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is True

@pytest.mark.asyncio
async def test_verify_crawler_data_partial_mismatch_allowed(task):
    """1개 종목 불일치 시에는 임계치(mismatch < 2)를 넘지 않아 검증을 통과하는지 확인"""
    df_crawled = pd.DataFrame({
        "종목코드": ["005930", "000660"],
        "종가": [80000, 150000],
        "시가": [79000, 140000],
        "고가": [81000, 160000],
        "저가": [78000, 130000]
    }).set_index("종목코드")

    async def mock_fetch(code, force_fresh=False):
        data = {
            "005930": {"stck_prpr": "80000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000"},
            "000660": {"stck_prpr": "151000", "stck_oprc": "140000", "stck_hgpr": "160000", "stck_lwpr": "130000"} # 종가 다름
        }
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={"output": data[code]})

    with patch.object(task, '_fetch_with_retry', side_effect=mock_fetch):
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is True

@pytest.mark.asyncio
async def test_verify_crawler_data_fail_mismatch(task):
    """API 응답과 크롤링 데이터가 2개 이상 불일치할 때 검증 실패 확인"""
    df_crawled = pd.DataFrame({
        "종목코드": ["005930", "000660"],
        "종가": [80000, 150000],
        "시가": [79000, 140000],
        "고가": [81000, 160000],
        "저가": [78000, 130000]
    }).set_index("종목코드")

    async def mock_fetch(code, force_fresh=False):
        data = {
            "005930": {"stck_prpr": "81000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000"}, # 다름
            "000660": {"stck_prpr": "151000", "stck_oprc": "140000", "stck_hgpr": "160000", "stck_lwpr": "130000"} # 다름
        }
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={"output": data[code]})

    with patch.object(task, '_fetch_with_retry', side_effect=mock_fetch):
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

# ── 추가된 Coverage 보완용 테스트 케이스 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_state_management(task):
    """start, suspend, resume 등 SchedulableTask 인터페이스 상태 관리 확인"""
    # start — 백그라운드 스케줄러가 실제 실행되지 않도록 create_task 패치
    with patch("asyncio.create_task"):
        await task.start()
    assert task._state == TaskState.RUNNING
    assert task._suspend_event.is_set()

    # suspend
    await task.suspend()
    assert task._state == TaskState.SUSPENDED
    assert not task._suspend_event.is_set()

    # resume
    await task.resume()
    assert task._state == TaskState.RUNNING
    assert task._suspend_event.is_set()

@pytest.mark.asyncio
async def test_on_market_closed_trigger(task):
    """장 마감 콜백 발생 시 이미 수집된 날짜가 아니면 파이프라인 트리거 확인"""
    task._last_collected_date = "2024-12-31"
    with patch.object(task, '_collect_all_prices', new_callable=AsyncMock) as mock_collect:
        await task._on_market_closed("2025-01-01")
        mock_collect.assert_called_once()
        
        # 이미 수집된 날짜면 스킵
        task._last_collected_date = "2025-01-01"
        mock_collect.reset_mock()
        await task._on_market_closed("2025-01-01")
        mock_collect.assert_not_called()

@pytest.mark.asyncio
async def test_force_run(task, mock_mcs):
    """force_run: FDR 없이 증권사 API(_collect_via_broker_api)를 직접 호출한다."""
    mock_mcs.is_market_open_now.return_value = False
    mock_mcs.get_latest_trading_date.return_value = "2025-01-01"
    with patch.object(task, '_collect_via_broker_api', new_callable=AsyncMock) as mock_broker, \
         patch.object(task, '_finish_collection', new_callable=AsyncMock), \
         patch.object(task, '_load_all_stocks', return_value=[]):
        await task.force_run()
        mock_broker.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_run_waits_for_ongoing_collection(task, mock_mcs):
    """force_run 호출 시 이미 수집 중이면 완료될 때까지 대기 후 반환한다."""
    import asyncio as _asyncio
    mock_mcs.get_latest_trading_date.return_value = "2025-01-01"

    # 수집 진행 중 상태 시뮬레이션
    task._is_collecting = True
    task._collection_done_event.clear()

    # 50ms 뒤 수집 완료 신호
    async def _signal_done():
        await _asyncio.sleep(0.05)
        task._is_collecting = False
        task._collection_done_event.set()

    _asyncio.create_task(_signal_done())

    with patch.object(task, '_collect_via_broker_api', new_callable=AsyncMock) as mock_broker:
        await task.force_run()
        # 진행 중 수집 완료를 기다렸으므로 새 수집은 시작하지 않음
        mock_broker.assert_not_awaited()

    assert task._collection_done_event.is_set()

@pytest.mark.asyncio
async def test_collect_all_prices_no_trading_date(task, mock_mcs):
    """최근 거래일 정보를 가져올 수 없으면 즉시 종료하는지 확인"""
    mock_mcs.is_market_open_now.return_value = False
    mock_mcs.get_latest_trading_date.return_value = None
    
    await task._collect_all_prices()
    assert task._is_collecting is False

@pytest.mark.asyncio
async def test_collect_all_prices_exception_handling(task, mock_mcs):
    """파이프라인 실행 중 예외 발생 시 finally 블록에서 상태가 잘 초기화되는지 확인"""
    with patch.object(task, '_try_collect_via_fdr', side_effect=Exception("Unexpected Error")):
        await task._collect_all_prices()
        # is_collecting 플래그 및 캐시가 초기화되어야 함
        assert task._is_collecting is False
        assert getattr(task, "_all_stocks_cache", None) is None

@pytest.mark.asyncio
async def test_fetch_with_retry_logic(task):
    """_fetch_with_retry: 실패 후 재시도 성공, 재시도 초과 실패 등 검증"""
    fail_resp = ResCommonResponse(rt_cd="1", msg1="Fail", data=None)
    success_resp = ResCommonResponse(rt_cd="0", msg1="OK", data={})
    
    # 시나리오 1: 1번 실패, 1번 Exception, 3번째에 성공
    task._stock_query_service.get_current_price.side_effect = [fail_resp, Exception("NetErr"), success_resp]
    with patch('asyncio.sleep', new_callable=AsyncMock):  # 딜레이 스킵
        res1 = await task._fetch_with_retry("005930")
        assert res1 == success_resp
        assert task._stock_query_service.get_current_price.call_count == 3
        
    # 시나리오 2: 전부 실패
    task._stock_query_service.get_current_price.reset_mock()
    task._stock_query_service.get_current_price.side_effect = [fail_resp, fail_resp, fail_resp]
    with patch('asyncio.sleep', new_callable=AsyncMock):
        res2 = await task._fetch_with_retry("005930")
        assert res2 is None
        assert task._stock_query_service.get_current_price.call_count == 3

def test_extract_broker_api_record_edge_cases(task):
    """API 응답 파싱 엣지 케이스 (None, 누락된 데이터, 형변환 오류 등)"""
    # 1. 응답이 없는 경우
    assert task._extract_broker_api_record("005930", "삼성", "KOSPI", None) is None
    
    # 2. data/output이 빈 경우
    resp_empty = ResCommonResponse(rt_cd="0", msg1="", data={})
    assert task._extract_broker_api_record("005930", "삼성", "KOSPI", resp_empty) is None
    
    # 3. 숫자로 변환 불가능한 값(문자열) 및 Inf/NaN 등이 섞여 있는 경우 예외 없이 기본값(0) 반환
    resp_bad_types = ResCommonResponse(rt_cd="0", msg1="", data={
        "output": {"stck_prpr": "ABC", "acml_vol": "N/A", "per": "Inf", "pbr": "NaN", "eps": "invalid"}
    })
    rec = task._extract_broker_api_record("005930", "삼성", "KOSPI", resp_bad_types)
    assert rec is not None
    assert rec["current_price"] == 0
    assert rec["volume"] == 0
    assert rec["per"] == 0.0
    assert rec["pbr"] == 0.0
    assert rec["eps"] == 0.0

@pytest.mark.asyncio
async def test_verify_crawler_data_missing_stock_in_crawled(task):
    """크롤링 데이터 안에 검증용 종목이 누락된 경우 스킵하고 나머지로 판별하는지 검증"""
    # 005930 누락, 000660만 존재
    df_crawled = pd.DataFrame({
        "종목코드": ["000660"], "종가": [150000], "시가": [140000], "고가": [160000], "저가": [130000]
    }).set_index("종목코드")

    async def mock_fetch(code, force_fresh=False):
        if code == "000660":
            data = {"stck_prpr": "150000", "stck_oprc": "140000", "stck_hgpr": "160000", "stck_lwpr": "130000"}
            return ResCommonResponse(rt_cd="0", msg1="", data={"output": data})
        return ResCommonResponse(rt_cd="1", msg1="Fail", data=None) # 005930 실패

    with patch.object(task, '_fetch_with_retry', side_effect=mock_fetch):
        result = await task._verify_crawler_data(df_crawled, "TEST")
        assert result is True # 005930은 스킵되고, 000660 1건 일치하므로 통과

@pytest.mark.asyncio
async def test_try_collect_via_fdr_empty_or_exception(task):
    """FDR 수집 결과가 비어있거나 스레드 내에서 예외가 발생할 경우 False 반환 검증"""
    with patch('asyncio.to_thread', new_callable=AsyncMock, return_value=pd.DataFrame()):
        assert await task._try_collect_via_fdr("2025-01-01", 0.0) is False
        
    with patch('asyncio.to_thread', new_callable=AsyncMock, side_effect=Exception("FDR Error")):
        assert await task._try_collect_via_fdr("2025-01-01", 0.0) is False

def test_format_dataframe_to_records_empty_and_error(task):
    """_format_dataframe_to_records의 빈 DF, 필수 컬럼 누락 시 예외처리 스킵 확인"""
    # 1. 빈 DF
    assert task._format_dataframe_to_records(pd.DataFrame()) == []
    assert task._format_dataframe_to_records(None) == []
    
    # 2. 필수 값 매핑 실패 시 (에러 발생 행 무시)
    df_bad = pd.DataFrame({
        "종목코드": ["005930"], 
        "종가": ["invalid_str"] # int 변환 시 예외 발생 유도
    })
    assert task._format_dataframe_to_records(df_bad) == []


def test_chunked_and_basic_properties(task):
    """헬퍼 함수 및 기본 프로퍼티 반환값 확인"""
    assert list(_chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert task.task_name == "daily_price_collector"
    assert task._scheduler_label == "DailyPriceCollector"


@pytest.mark.asyncio
async def test_suspend_resume_noop_when_state_not_matching(task):
    """RUNNING/SUSPENDED 상태가 아니면 suspend/resume이 무시되는지 확인"""
    task._state = TaskState.STOPPED
    task._suspend_event.set()

    await task.suspend()
    assert task._state == TaskState.STOPPED
    assert task._suspend_event.is_set()

    await task.resume()
    assert task._state == TaskState.STOPPED
    assert task._suspend_event.is_set()


@pytest.mark.asyncio
async def test_collect_all_prices_skip_when_already_collecting(task):
    """이미 수집 중이면 즉시 스킵하는지 확인"""
    task._is_collecting = True

    with patch.object(task, "_collect_via_broker_api", new_callable=AsyncMock) as mock_collect:
        await task._collect_all_prices()
        mock_collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_all_prices_broker_api_exception_resets_state(task):
    """Broker API 수집 예외 시에도 상태와 이벤트가 정리되는지 확인"""
    with patch.object(task, "_load_all_stocks", return_value=[]), \
         patch.object(task, "_collect_via_broker_api", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await task._collect_all_prices()

    assert task._is_collecting is False
    assert task._collection_done_event.is_set()
    assert task._all_stocks_cache is None


@pytest.mark.asyncio
async def test_verify_crawler_data_uses_code_column_and_handles_parse_error(task):
    """인덱스가 아닌 종목코드 컬럼 탐색 및 파싱 예외 스킵 분기 확인"""
    df_crawled = pd.DataFrame({
        "종목코드": ["005930", "000660"],
        "종가": ["bad", 150000],
        "시가": [79000, 140000],
        "고가": [81000, 160000],
        "저가": [78000, 130000],
    })

    async def mock_fetch(code, force_fresh=False):
        data = {
            "005930": {"stck_prpr": "80000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000"},
            "000660": {"stck_prpr": "150000", "stck_oprc": "140000", "stck_hgpr": "160000", "stck_lwpr": "130000"},
        }
        return ResCommonResponse(rt_cd="0", msg1="", data={"output": data[code]})

    with patch.object(task, "_fetch_with_retry", side_effect=mock_fetch):
        assert await task._verify_crawler_data(df_crawled, "TEST") is True


@pytest.mark.asyncio
async def test_try_collect_via_fdr_executes_sync_fetch_and_rename(task):
    """to_thread 내부 동기 함수가 실제 실행되어 rename 경로를 타는지 확인"""
    df_fdr = pd.DataFrame({"Code": ["005930"], "Close": [80000]})

    async def run_sync(func):
        return func()

    with patch("task.background.after_market.daily_price_collector_task.fdr.StockListing", return_value=df_fdr.copy()), \
         patch("asyncio.to_thread", new=run_sync), \
         patch.object(task, "_verify_crawler_data", new_callable=AsyncMock, return_value=True) as mock_verify, \
         patch.object(task, "_format_dataframe_to_records", return_value=[{"code": "005930"}]), \
         patch.object(task, "_save_bulk_to_db_with_progress", new_callable=AsyncMock):
        assert await task._try_collect_via_fdr("2025-01-01", 0.0) is True

    passed_df = mock_verify.await_args.args[0]
    assert "종목코드" in passed_df.columns
    assert "종가" in passed_df.columns


@pytest.mark.asyncio
async def test_collect_via_broker_api_skips_sleep_for_cache_hits_and_flushes_tail(task):
    """모든 응답이 캐시 히트면 sleep을 건너뛰고 마지막 잔여 버퍼를 저장하는지 확인"""
    task.API_CHUNK_SIZE = 1
    task.DB_UPSERT_BATCH_SIZE = 5
    cached_resp = ResCommonResponse(
        rt_cd="0",
        msg1="",
        data={"output": {"stck_prpr": "80000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000"}},
    )
    cached_resp._cache_hit = True

    with patch.object(task, "_load_all_stocks", return_value=[("005930", "삼성전자", "KOSPI")]), \
         patch.object(task, "_fetch_with_retry", new_callable=AsyncMock, return_value=cached_resp), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await task._collect_via_broker_api("2025-01-01", 0.0)

    mock_sleep.assert_not_awaited()
    task._stock_repo.upsert_daily_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_via_broker_api_handles_exception_response(task):
    """gather 결과에 예외 객체가 포함되어도 나머지 레코드는 저장하는지 확인"""
    task.API_CHUNK_SIZE = 2
    task.DB_UPSERT_BATCH_SIZE = 10
    success_resp = ResCommonResponse(
        rt_cd="0",
        msg1="",
        data={"output": {"stck_prpr": "80000", "stck_oprc": "79000", "stck_hgpr": "81000", "stck_lwpr": "78000"}},
    )

    with patch.object(task, "_load_all_stocks", return_value=[("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI")]), \
         patch.object(task, "_fetch_with_retry", new_callable=AsyncMock, side_effect=[success_resp, RuntimeError("api error")]), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await task._collect_via_broker_api("2025-01-01", 0.0)

    saved_batch = task._stock_repo.upsert_daily_snapshot.await_args.args[1]
    assert len(saved_batch) == 1
    assert saved_batch[0]["code"] == "005930"


@pytest.mark.asyncio
async def test_finish_collection_with_rs_rating_success_and_failure(task):
    """RS Rating 후처리 성공/실패 분기와 알림 없는 경로를 함께 확인"""
    task._ns = None
    task._rs_rating_service = AsyncMock()
    task._rs_rating_service.compute_and_store_ratings.return_value = SimpleNamespace(msg1="done")

    await task._finish_collection("2025-01-01", 0.0, "TEST")
    task._rs_rating_service.compute_and_store_ratings.assert_awaited_once_with("2025-01-01")

    task._rs_rating_service.compute_and_store_ratings.reset_mock(side_effect=True)
    task._rs_rating_service.compute_and_store_ratings.side_effect = RuntimeError("rs fail")
    await task._finish_collection("2025-01-02", 0.0, "TEST")
    assert task._last_collected_date == "2025-01-02"


def test_extract_broker_api_record_object_output_and_failure_paths(task):
    """객체 output 처리 및 output/예외 분기 확인"""
    resp_with_object = ResCommonResponse(
        rt_cd="0",
        msg1="",
        data=SimpleNamespace(
            stck_prpr="70000",
            stck_oprc="69000",
            stck_hgpr="71000",
            stck_lwpr="68000",
            stck_sdpr="69500",
            prdy_vrss="500",
            prdy_vrss_sign="2",
            prdy_ctrt="0.72",
            acml_vol="1234",
            acml_tr_pbmn="999999",
            hts_avls="555555",
            per="11.1",
            pbr="1.2",
            eps="123",
            w52_hgpr="90000",
            w52_lwpr="60000",
        ),
    )
    record = task._extract_broker_api_record("005930", "삼성전자", "KOSPI", resp_with_object)
    assert record["current_price"] == 70000
    assert record["market"] == "KOSPI"

    resp_without_output = ResCommonResponse(rt_cd="0", msg1="", data={"output": None})
    assert task._extract_broker_api_record("005930", "삼성전자", "KOSPI", resp_without_output) is None

    class BrokenOutput:
        def __getattr__(self, name):
            raise RuntimeError("broken")

    resp_broken = ResCommonResponse(rt_cd="0", msg1="", data=BrokenOutput())
    assert task._extract_broker_api_record("005930", "삼성전자", "KOSPI", resp_broken) is None


def test_get_progress_and_zero_change_record(task):
    """진행률 복사본 반환 및 보합 종목 change_sign 계산 확인"""
    progress = task.get_progress()
    progress["processed"] = 999
    assert task._progress["processed"] != 999

    df = pd.DataFrame({
        "종목코드": ["005930"],
        "종가": [80000],
        "시가": [80000],
        "고가": [80500],
        "저가": [79500],
        "거래량": [1000],
        "거래대금": [80000000],
        "시가총액": [400000000000],
        "대비": [0],
        "등락률": [0],
    })
    record = task._format_dataframe_to_records(df)[0]
    assert record["change_sign"] == "3"


@pytest.mark.asyncio
async def test_force_run_no_trading_date_and_exception_cleanup(task, mock_mcs):
    """force_run의 거래일 없음/예외 발생 분기 확인"""
    mock_mcs.get_latest_trading_date.return_value = None
    await task.force_run()
    assert task._is_collecting is False

    mock_mcs.get_latest_trading_date.return_value = "2025-01-03"
    with patch.object(task, "_load_all_stocks", return_value=[]), \
         patch.object(task, "_collect_via_broker_api", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await task.force_run(force_fresh=True)

    assert task._is_collecting is False
    assert task._collection_done_event.is_set()
    assert task._all_stocks_cache is None


@pytest.mark.asyncio
async def test_save_bulk_to_db_with_progress_empty_records(task):
    """저장 대상이 없으면 DB 호출 없이 반환하는지 확인"""
    await task._save_bulk_to_db_with_progress("2025-01-01", [], 0.0)
    task._stock_repo.upsert_daily_snapshot.assert_not_called()
