import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from api.base import _KoreaInvestAPIBase
import logging

class DummyAPI(_KoreaInvestAPIBase):
    # _call_api는 상속받아 그대로 사용
    pass

@pytest.mark.asyncio
async def test_call_api_retry_on_rate_limit(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    logger = logging.getLogger("test_logger")

    api = DummyAPI(base_url, headers, config, logger)

    # 응답 객체 모킹 (500 + 초당 거래건수 초과)
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    # requests.Session.get 모킹 (첫 2회는 실패, 3회째는 정상)
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"rt_cd": "0", "output": {"data": "success"}}
    success_response.text = '{"rt_cd":"0","output":{"data":"success"}}'
    success_response.raise_for_status = MagicMock()

    call_count = 0
    def side_effect_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return mock_response
        else:
            return success_response

    api._session.get = MagicMock(side_effect=side_effect_get)

    with caplog.at_level(logging.WARNING):
        result = await api._call_api('GET', '/dummy-path', retry_count=3, delay=0.01)

    # 정상 응답이 리턴되는지 확인
    assert result == {"rt_cd": "0", "output": {"data": "success"}}

    # 재시도 로그가 2번 이상 기록되었는지 확인
    warnings = [rec for rec in caplog.records if rec.levelname == "WARNING"]
    assert any("초당 거래건수 초과 오류 감지" in rec.message for rec in warnings)

    # 총 3회 호출되었는지 확인
    assert call_count == 3

@pytest.mark.asyncio
async def test_call_api_retry_exceed_failure(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    logger = logging.getLogger("test_logger")

    api = DummyAPI(base_url, headers, config, logger)

    # 항상 500 + 초당 거래건수 초과 응답만 반환
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    api._session.get = MagicMock(return_value=mock_response)

    with caplog.at_level(logging.ERROR):
        result = await api._call_api('GET', '/dummy-path', retry_count=2, delay=0.01)

    # 실패시 None 리턴
    assert result is None

    # 오류 로그가 기록됐는지 확인
    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("재시도 횟수 초과" in rec.message for rec in errors)
