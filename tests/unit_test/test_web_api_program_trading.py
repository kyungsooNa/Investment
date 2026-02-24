import pytest
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.mark.asyncio
async def test_subscribe_program_trading(web_client, mock_web_ctx):
    """POST /api/program-trading/subscribe 엔드포인트 테스트"""
    
    # 프로그램 매매 관련 상태 초기화
    mock_web_ctx._pt_codes = set()
    mock_web_ctx._pt_queues = []

    # 구독 성공 Mocking
    mock_web_ctx.start_program_trading = AsyncMock(return_value=True)
    mock_web_ctx._pt_codes = {"005930"} # 구독 후 상태 시뮬레이션
    
    response = web_client.post("/api/program-trading/subscribe", json={"code": "005930"})
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "005930" in response.json()["codes"]
    mock_web_ctx.start_program_trading.assert_awaited_once_with("005930")

def test_save_pt_data(web_client, mock_web_ctx):
    """POST /api/program-trading/save-data 엔드포인트 테스트 (파일 저장)"""
    
    # 파일 시스템 조작 Mocking
    with patch("builtins.open", new_callable=MagicMock) as mock_open, \
         patch("os.makedirs") as mock_makedirs, \
         patch("json.dump") as mock_json_dump:
        
        data = {
            "chartData": {"005930": {"valueData": []}},
            "subscribedCodes": ["005930"],
            "codeNameMap": {"005930": "삼성전자"}
        }
        
        response = web_client.post("/api/program-trading/save-data", json=data)
        
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_makedirs.assert_called_with("data", exist_ok=True)
        mock_open.assert_called_with("data/pt_data.json", "w", encoding="utf-8")
        mock_json_dump.assert_called()

def test_load_pt_data_success(web_client, mock_web_ctx):
    """GET /api/program-trading/load-data 엔드포인트 테스트 (성공 시)"""
    
    mock_data = {"chartData": {}, "subscribedCodes": []}
    
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", new_callable=MagicMock) as mock_open, \
         patch("json.load", return_value=mock_data):
        
        response = web_client.get("/api/program-trading/load-data")
        
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"] == mock_data

def test_load_pt_data_file_not_found(web_client, mock_web_ctx):
    """GET /api/program-trading/load-data 엔드포인트 테스트 (파일 없음)"""
    with patch("os.path.exists", return_value=False):
        response = web_client.get("/api/program-trading/load-data")
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert response.json()["msg"] == "File not found"