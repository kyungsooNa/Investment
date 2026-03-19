"""
시스템 상태 및 캐시 모니터링 관련 테스트.
"""

def test_get_cache_status(web_client, mock_web_ctx):
    """GET /api/cache/status 엔드포인트 테스트"""
    mock_web_ctx.get_cache_stats.return_value = {
        "hits": 100, "misses": 5, "hit_rate": 95.24, "total_requests": 105, "current_size": 50
    }
    
    response = web_client.get("/api/cache/status")
    
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["success"] is True
    assert json_resp["data"]["hits"] == 100
    assert json_resp["data"]["hit_rate"] == 95.24