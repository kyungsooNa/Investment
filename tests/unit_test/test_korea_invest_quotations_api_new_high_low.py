
import pytest
from unittest.mock import MagicMock, AsyncMock

from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from common.types import ResStockFullInfoApiOutput, ResCommonResponse, ErrorCode

@pytest.fixture
def mock_api_quotations():
    """KoreaInvestApiQuotations의 mock 객체를 생성합니다."""
    mock_env = MagicMock()
    mock_logger = MagicMock()
    mock_time_manager = MagicMock()
    api = KoreaInvestApiQuotations(env=mock_env, logger=mock_logger, time_manager=mock_time_manager)
    return api

@pytest.mark.asyncio
async def test_get_price_summary_new_high_low_mismatch(mock_api_quotations):
    """
    데이터상 신고가 조건이지만 API 코드가 아닐 때, 현재 로직은 불일치를 감지하지 않고 API 코드를 따르는지 테스트합니다.
    """
    stock_code = "005930"

    # API 응답 모의
    raw_output = {
        'stck_oprc': '80000',
        'stck_prpr': '85000',
        'prdy_ctrt': '6.25',
        'stck_hgpr': '86000',
        'w52_hgpr': '85000',
        'd250_hgpr': '84000',
        'new_hgpr_lwpr_cls_code': '4',
    }
    output_obj = ResStockFullInfoApiOutput.from_dict(raw_output)
    
    # get_current_price가 반환할 ResCommonResponse 모의
    mock_api_quotations.get_current_price = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="Success",
            data={'output': output_obj}
        )
    )

    # get_price_summary 호출
    result = await mock_api_quotations.get_price_summary(stock_code)
    
    # 결과 검증
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data.new_high_low_status == "-"

    # logger.warning이 호출되지 않아야 함 (현재 로직 기준)
    mock_api_quotations._logger.warning.assert_not_called()
    
@pytest.mark.asyncio
async def test_get_price_summary_new_high_match(mock_api_quotations):
    """
    is_new_high가 True이고 API에서도 신고가로 응답할 때 정상 처리되는지 테스트합니다.
    """
    stock_code = "005930"

    # API 응답 모의 (신고가 상황)
    raw_output = {
        'stck_oprc': '80000',
        'stck_prpr': '85000',
        'prdy_ctrt': '6.25',
        'stck_hgpr': '86000',
        'w52_hgpr': '86000',
        'd250_hgpr': '85000',
        'new_hgpr_lwpr_cls_code': '1',
    }
    output_obj = ResStockFullInfoApiOutput.from_dict(raw_output)

    mock_api_quotations.get_current_price = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="Success",
            data={'output': output_obj}
        )
    )

    result = await mock_api_quotations.get_price_summary(stock_code)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data.new_high_low_status == "신고가"
    mock_api_quotations._logger.warning.assert_not_called()
    
@pytest.mark.asyncio
async def test_get_price_summary_new_low_mismatch(mock_api_quotations):
    """
    데이터상 신저가 조건이지만 API 코드가 아닐 때, 현재 로직은 불일치를 감지하지 않고 API 코드를 따르는지 테스트합니다.
    """
    stock_code = "005930"

    # API 응답 모의
    raw_output = {
        'stck_oprc': '80000',
        'stck_prpr': '75000',
        'prdy_ctrt': '-6.25',
        'stck_lwpr': '74000',
        'w52_lwpr': '75000',
        'd250_lwpr': '76000',
        'new_hgpr_lwpr_cls_code': '4',
    }
    output_obj = ResStockFullInfoApiOutput.from_dict(raw_output)
    
    mock_api_quotations.get_current_price = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="Success",
            data={'output': output_obj}
        )
    )

    result = await mock_api_quotations.get_price_summary(stock_code)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data.new_high_low_status == "-"

    mock_api_quotations._logger.warning.assert_not_called()

@pytest.mark.asyncio
async def test_get_price_summary_new_low_match(mock_api_quotations):
    """
    is_new_low가 True이고 API에서도 신저가로 응답할 때 정상 처리되는지 테스트합니다.
    """
    stock_code = "005930"

    # API 응답 모의 (신저가 상황)
    raw_output = {
        'stck_oprc': '80000',
        'stck_prpr': '75000',
        'prdy_ctrt': '-6.25',
        'stck_lwpr': '74000',
        'w52_lwpr': '74000',
        'd250_lwpr': '75000',
        'new_hgpr_lwpr_cls_code': '2',
    }
    output_obj = ResStockFullInfoApiOutput.from_dict(raw_output)

    mock_api_quotations.get_current_price = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="Success",
            data={'output': output_obj}
        )
    )

    result = await mock_api_quotations.get_price_summary(stock_code)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data.new_high_low_status == "신저가"
    mock_api_quotations._logger.warning.assert_not_called()
    
