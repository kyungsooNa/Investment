import pytest
from unittest.mock import MagicMock, AsyncMock
from services.trading_service import TradingService
from common.types import ResCommonResponse, ErrorCode

@pytest.fixture
def mock_deps(mocker):
    """TradingService의 의존성 Mock 객체 생성"""
    broker = mocker.Mock()
    env = mocker.Mock()
    logger = mocker.Mock()
    time_manager = mocker.Mock()
    return broker, env, logger, time_manager

@pytest.fixture
def trading_service(mock_deps):
    """TradingService 인스턴스 생성"""
    broker, env, logger, time_manager = mock_deps
    return TradingService(broker, env, logger, time_manager)

def make_response(data):
    """ResCommonResponse 헬퍼"""
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data=data
    )

@pytest.mark.asyncio
async def test_get_top_trading_value_stocks_logic(trading_service):
    """
    [Unit Test] 거래대금 상위 종목 조회 로직 검증
    1. 5개 소스(거래량, 코스피시총, 코스닥시총, 상승, 하락) 데이터 병합
    2. ETF/ETN 종목 제외 (KODEX 등)
    3. 거래대금(acml_tr_pbmn) 기준 내림차순 정렬
    4. 상위 30개 절삭 확인
    """
    broker = trading_service._broker_api_wrapper

    # --- 1. Mock Data 준비 ---
    # 각 API가 반환할 가짜 데이터 (중복 포함, 순서 뒤죽박죽)
    
    # A: 일반 종목 (대금 100억)
    item_a = {"mksc_shrn_iscd": "000001", "hts_kor_isnm": "삼성전자", "acml_tr_pbmn": "10000000000"} 
    # B: 일반 종목 (대금 500억) - 거래량 랭킹에서 발견
    item_b = {"mksc_shrn_iscd": "000002", "hts_kor_isnm": "SK하이닉스", "acml_tr_pbmn": "50000000000"}
    # C: ETF 종목 (대금 900억) -> 제외되어야 함
    item_c = {"mksc_shrn_iscd": "000003", "hts_kor_isnm": "KODEX 200", "acml_tr_pbmn": "90000000000"}
    # D: 일반 종목 (대금 300억) - 시총 랭킹에서 발견 (acml_tr_pbmn 없을 수 있음 -> 계산 로직 테스트)
    #    TradingService 로직상 acml_tr_pbmn이 없으면 stck_prpr * acml_vol로 계산함
    item_d = {"mksc_shrn_iscd": "000004", "hts_kor_isnm": "NAVER", "stck_prpr": "200000", "acml_vol": "150000"} 
    #    계산값: 200,000 * 150,000 = 30,000,000,000 (300억)

    # E: 일반 종목 (대금 10억)
    item_e = {"stck_shrn_iscd": "000005", "hts_kor_isnm": "카카오", "acml_tr_pbmn": "1000000000"}

    # Mock Return Values 설정 (AsyncMock)
    # 1. 거래량 상위
    broker.get_top_volume_stocks = AsyncMock(return_value=make_response({"output": [item_a, item_b]}))
    
    # 2. 코스피/코스닥 시총 (인자에 따라 다른 값 반환)
    async def get_mc_side_effect(market_code, limit):
        if market_code == "0000": # 코스피
            return make_response([item_c, item_d])
        return make_response([]) # 코스닥
    broker.get_top_market_cap_stocks_code = AsyncMock(side_effect=get_mc_side_effect)

    # 3. 상승/하락 (인자에 따라 다른 값 반환)
    async def get_rise_fall_side_effect(rise):
        if rise: # 상승
            return make_response([item_b, item_e])
        return make_response([]) # 하락
    broker.get_top_rise_fall_stocks = AsyncMock(side_effect=get_rise_fall_side_effect)

    # --- 2. 실행 ---
    response = await trading_service.get_top_trading_value_stocks()

    # --- 3. 검증 ---
    assert response.rt_cd == ErrorCode.SUCCESS.value
    result_list = response.data

    # 3-1. ETF 제외 확인 (item_c "KODEX 200" 제외)
    codes = [item.get("mksc_shrn_iscd") or item.get("stck_shrn_iscd") or item.get("iscd") for item in result_list]
    assert "000003" not in codes

    # 3-2. 정렬 순서 확인 (거래대금 내림차순)
    # 예상 순서: 
    # 1위: B (500억)
    # 2위: D (300억 - 계산됨)
    # 3위: A (100억)
    # 4위: E (10억)
    expected_order = ["000002", "000004", "000001", "000005"]
    assert codes == expected_order

    # 3-3. 데이터 병합 확인 (B가 여러 API에서 나왔지만 하나만 존재해야 함)
    assert codes.count("000002") == 1

    # 3-4. 계산 로직 확인 (D의 거래대금이 계산되었는지)
    item_d_result = next(item for item in result_list if item.get("mksc_shrn_iscd") == "000004")
    assert str(item_d_result.get("acml_tr_pbmn")) == "30000000000"


def test_is_etf_logic(trading_service):
    """
    [Unit Test] _is_etf 메서드의 필터링 로직 검증
    """
    # ETF/ETN 케이스
    assert trading_service._is_etf({"hts_kor_isnm": "KODEX 200"}) is True
    assert trading_service._is_etf({"hts_kor_isnm": "TIGER 미국테크TOP10"}) is True
    assert trading_service._is_etf({"hts_kor_isnm": "KBSTAR 단기통안채"}) is True
    assert trading_service._is_etf({"hts_kor_isnm": "SOL 미국S&P500"}) is True
    assert trading_service._is_etf({"hts_kor_isnm": "ACE 미국30년국채"}) is True
    
    # 일반 종목 케이스
    assert trading_service._is_etf({"hts_kor_isnm": "삼성전자"}) is False
    assert trading_service._is_etf({"hts_kor_isnm": "SK하이닉스"}) is False
    assert trading_service._is_etf({"hts_kor_isnm": "NAVER"}) is False
    
    # 예외 케이스 (이름 없음)
    assert trading_service._is_etf({}) is False
    assert trading_service._is_etf({"hts_kor_isnm": None}) is False