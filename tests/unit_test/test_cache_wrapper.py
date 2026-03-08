# test_cache_wrapper.py

import pytest
import json
import pytz
from unittest.mock import MagicMock, AsyncMock, patch
from core.cache.cache_wrapper import cache_wrap_client, ClientWithCache
from core.cache.cache_manager import CacheManager
from datetime import datetime, timedelta
from unittest.mock import ANY
from common.types import ResCommonResponse, ErrorCode

fake_time = (datetime.now() - timedelta(days=1)).isoformat()


class DummyApiClient:
    async def get_data(self, x):
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"key": f"value-{x}"}
        )
    async def bypass_data(self, y):
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"key": f"no_cache-{y}"}
        )

@pytest.mark.asyncio
async def test_cache_wrapper_hit_and_miss(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    # KST 타임존 aware datetime
    seoul_tz = pytz.timezone("Asia/Seoul")
    time_manager.market_timezone = seoul_tz  # <- 여기!!

    now = seoul_tz.localize(datetime.now())
    close_time = now - timedelta(minutes=5)
    next_open_time = now + timedelta(hours=8)

    time_manager.get_latest_market_close_time.return_value = close_time
    time_manager.get_current_kst_time.return_value = now
    time_manager.get_next_market_open_time.return_value = next_open_time  # ✅ 이 부분 추가

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    # 최초 호출 → 캐시 없음
    result1 = await wrapped.get_data(1)
    # 두 번째 호출 → 캐시 HIT 기대
    result2 = await wrapped.get_data(1)

    # ✅ 결과 값 검증
    assert result1.data.get('key') == "value-1"
    assert result2.data.get('key') == "value-1"

    # ✅ 로그 메시지 검증
    debug_logs = [call.args[0] for call in logger.debug.call_args_list]

    assert any("Memory Cache MISS" in msg for msg in debug_logs)
    assert any("File Cache MISS" in msg for msg in debug_logs)
    assert any("Memory Cache HIT" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_cache_wrapper_expired_cache_removal(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    # KST 타임존 aware datetime
    seoul_tz = pytz.timezone("Asia/Seoul")
    time_manager.market_timezone = seoul_tz

    now = seoul_tz.localize(datetime.now())
    close_time = now - timedelta(minutes=5)

    # ✅ 추가
    next_open_time = now + timedelta(hours=8)

    time_manager.get_latest_market_close_time.return_value = close_time
    time_manager.get_current_kst_time.return_value = now
    time_manager.get_next_market_open_time.return_value = next_open_time  # ✅ 이 부분 추가


    # 👉 시장은 닫혀 있고, 캐시 만료 여부를 확인할 수 있게 구성
    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config
    )

    # 1. 최초 호출 → 캐시 저장
    result1 = await wrapped.get_data(1)
    assert result1.data.get('key') == "value-1"

    # 2. 파일 직접 열어서 timestamp를 하루 전으로 조작
    key = "TEST_get_data_1"
    file_path = cache_manager.file_cache._get_path(key)
    with open(file_path, "r+", encoding="utf-8") as f:
        payload = json.load(f)
        payload["timestamp"] = (datetime.now() - timedelta(days=1)).isoformat()

        f.seek(0)
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.truncate()

    # ✅ 메모리 캐시 제거 → 파일 캐시를 다시 조회하게 유도
    wrapped._cache.memory_cache.clear()

    # 3. 재호출 → 만료되어 새로 생성되어야 함
    result2 = await wrapped.get_data(1)
    assert result2.data.get('key') == "value-1"

    debug_logs = [call.args[0] for call in logger.debug.call_args_list]
    assert any("File Cache 무시 (만료됨)" in log for log in debug_logs)
    assert any("File cache 삭제됨" in log for log in debug_logs)


@pytest.mark.asyncio
async def test_cache_wrapper_bypass_for_non_cached_method(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    result = await wrapped.bypass_data(9)
    assert result.data.get('key') == "no_cache-9"

    assert any("Bypass" in call.args[0] for call in logger.debug.call_args_list)


def test_cache_wrapper_dir_includes_wrapped_methods(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    dir_list = dir(wrapped)

    # DummyApiClient에 있는 메서드들이 포함되어야 함
    assert "get_data" in dir_list
    assert "bypass_data" in dir_list

    # 내부 속성도 포함되어야 함
    assert "_client" in dir_list
    assert "_logger" in dir_list


@pytest.mark.asyncio
async def test_cache_wrapper_key_arg_str_empty_skip_append(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    # ✅ 공백 문자열을 인자로 넘겨 arg_str이 비어 key_parts.append() 안 타도록 유도
    result = await wrapped.get_data("")
    assert result.data.get('key') == "value-"

    # ✅ logger.debug 메시지를 기반으로 key_parts에 arg_str이 포함되지 않았는지 검증
    debug_msgs = [call.args[0] for call in logger.debug.call_args_list]
    assert any("TEST_get_data" in msg for msg in debug_msgs)


@pytest.mark.asyncio
async def test_client_with_cache_file_save(cache_manager, test_cache_config, tmp_path):
    base_dir = tmp_path
    config = {
        "cache": {
            "base_dir": str(base_dir),
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }
    cache_manager = CacheManager(config=config)

    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False  # => 파일 저장 조건

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=config  # ✅ 반드시 함께 전달해야 함
    )

    data = 7
    result = await wrapped.get_data(data)
    assert result.data.get('key') == f"value-{data}"

    expected_file = tmp_path / "TEST_get_data_7.json"
    assert expected_file.exists()

    with open(expected_file, encoding="utf-8") as f:
        payload = json.load(f)

        assert payload["data"]["rt_cd"] == "0"
        assert payload["data"]["msg1"] == "정상"
        assert payload["data"]["data"]["key"] == f"value-{data}"
        assert "timestamp" in payload


class _DummyApiClientForBypass:
    async def get_data(self, x):
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={"key": f"value-{x}"})

@pytest.mark.asyncio
async def test_cache_wrapper_caching_disabled_bypass(tmp_path):
    """둘 다 OFF면 매 호출마다 API로 바로 가야 하고 파일이 생성되면 안 됨"""
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": ["get_data"],
            "deserializable_classes": [],
            "memory_cache_enabled": False,
            "file_cache_enabled": False,
        }
    }
    cm = CacheManager(config=config)

    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False  # 상관없지만 일관성 유지

    wrapped = cache_wrap_client(
        api_client=_DummyApiClientForBypass(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cm,
        config=config
    )

    # 내부 API 메서드 호출 횟수 추적(실제로 두 번 호출되어야 함: 캐시가 없으므로)
    wrapped._client.get_data = AsyncMock(side_effect=wrapped._client.get_data)

    r1 = await wrapped.get_data(1)
    r2 = await wrapped.get_data(1)
    assert r1.data["key"] == "value-1"
    assert r2.data["key"] == "value-1"
    assert wrapped._client.get_data.await_count == 2  # 캐싱 없음

    # 로그 확인
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("Caching disabled" in msg for msg in debug_logs)

    # 파일 생성되지 않아야 함
    assert not (tmp_path / "TEST_get_data_1.json").exists()

class _DummyApiClient:
    async def get_data(self, x):
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={"key": f"value-{x}"})

@pytest.mark.asyncio
async def test_cache_wrapper_memory_off_file_on_reads_file(tmp_path):
    """메모리 OFF / 파일 ON → 두 번째 호출 시 파일 캐시 HIT"""
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": ["get_data"],
            "deserializable_classes": [],
            "memory_cache_enabled": False,
            "file_cache_enabled": True,
        }
    }
    cm = CacheManager(config=config)

    logger = MagicMock()
    tz = pytz.timezone("Asia/Seoul")
    now = tz.localize(datetime.now())
    time_manager = MagicMock(
        is_market_open=MagicMock(return_value=False),
        market_timezone=tz,
        get_latest_market_close_time=MagicMock(return_value=now - timedelta(minutes=5)),
        get_next_market_open_time=MagicMock(return_value=now + timedelta(hours=8)),
        get_current_kst_time=MagicMock(return_value=now),
    )

    wrapped = cache_wrap_client(
        api_client=_DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cm,
        config=config
    )

    # 1st call: save to file
    await wrapped.get_data(7)
    # 메모리 캐시가 없으므로, 2nd call은 파일 캐시 경로로 HIT
    await wrapped.get_data(7)

    # 파일 존재 확인
    expected = tmp_path / "TEST_get_data_7.json"
    assert expected.exists()

    # 로그에 File Cache HIT 포함
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("File Cache HIT" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_cache_wrapper_file_off_memory_on_hits_memory(tmp_path):
    """메모리 ON / 파일 OFF → 두 번째 호출 시 메모리 캐시 HIT, 파일은 생기지 않음"""
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": ["get_data"],
            "deserializable_classes": [],
            "memory_cache_enabled": True,
            "file_cache_enabled": False,
        }
    }
    cm = CacheManager(config=config)

    logger = MagicMock()
    tz = pytz.timezone("Asia/Seoul")
    now = tz.localize(datetime.now())
    time_manager = MagicMock(
        is_market_open=MagicMock(return_value=False),
        market_timezone=tz,
        get_latest_market_close_time=MagicMock(return_value=now - timedelta(minutes=5)),
        get_next_market_open_time=MagicMock(return_value=now + timedelta(hours=8)),
        get_current_kst_time=MagicMock(return_value=now),
    )

    wrapped = cache_wrap_client(
        api_client=_DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cm,
        config=config
    )

    await wrapped.get_data(5)   # 캐시 저장(메모리만)
    await wrapped.get_data(5)   # 메모리 HIT 기대

    # 파일이 없어야 함
    assert not (tmp_path / "TEST_get_data_5.json").exists()

    # 로그에 Memory Cache HIT 포함
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("Memory Cache HIT" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_init_default_config_loading():
    """config가 None일 때 load_cache_config 호출 확인"""
    with patch("core.cache.cache_wrapper.load_cache_config") as mock_load:
        mock_load.return_value = {
            "cache": {
                "base_dir": "dummy",
                "enabled_methods": [],
                "file_cache_enabled": False,
                "memory_cache_enabled": False
            }
        }
        # cache_manager도 None으로 주어서 내부 생성 유도
        with patch("core.cache.cache_wrapper.CacheManager"):
            ClientWithCache(
                client=MagicMock(),
                logger=MagicMock(),
                time_manager=MagicMock(),
                mode_fn=lambda: "TEST",
                cache_manager=None,
                config=None
            )
        mock_load.assert_called_once()

def test_getattr_private_attr(cache_manager, test_cache_config):
    """_로 시작하는 속성 접근 테스트"""
    client = DummyApiClient()
    wrapped = cache_wrap_client(
        api_client=client,
        logger=MagicMock(),
        time_manager=MagicMock(),
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config
    )
    # _client는 ClientWithCache의 속성임
    assert wrapped._client == client
    # 존재하지 않는 _private 속성 접근 시 AttributeError 발생해야 함 (object.__getattribute__ 동작)
    with pytest.raises(AttributeError):
        _ = wrapped._non_existent_private

@pytest.mark.asyncio
async def test_market_open_bypasses_cache(cache_manager, test_cache_config):
    """시장이 열려있으면 캐시가 있어도 API를 호출해야 함"""
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = True  # 시장 열림

    client = DummyApiClient()
    # API 호출 여부 확인을 위해 spy
    client.get_data = AsyncMock(wraps=client.get_data)

    wrapped = cache_wrap_client(
        api_client=client,
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config
    )

    # 1. 캐시 강제 주입
    key = "TEST_get_data_999"
    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": {"key": "cached_value"}
    })

    # 2. 호출
    result = await wrapped.get_data(999)

    # 3. 검증
    # 캐시된 값이 아니라 API 리턴값이어야 함 ("value-999")
    assert result.data["key"] == "value-999"
    # API가 실제로 호출되었어야 함
    client.get_data.assert_awaited_once()
    # 로그 확인
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("시장 개장 중 → 캐시 우회" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_parse_timestamp_edge_cases(cache_manager, test_cache_config):
    """_parse_timestamp 메서드의 다양한 케이스 테스트"""
    logger = MagicMock()
    time_manager = MagicMock()
    seoul_tz = pytz.timezone("Asia/Seoul")
    time_manager.market_timezone = seoul_tz

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config
    )

    # 1. None or Empty
    assert wrapped._parse_timestamp(None) is None
    assert wrapped._parse_timestamp("") is None

    # 2. Invalid Format
    assert wrapped._parse_timestamp("invalid-date") is None
    logger.warning.assert_called()
    assert "잘못된 timestamp 포맷" in logger.warning.call_args[0][0]

    # 3. Naive Datetime (timezone 없는 경우 localize 호출 확인)
    naive_iso = "2025-01-01T12:00:00"
    dt = wrapped._parse_timestamp(naive_iso)
    assert dt is not None
    assert dt.tzinfo is not None # localize 되었는지 확인
    assert str(dt.tzinfo) == str(seoul_tz)

@pytest.mark.asyncio
async def test_api_failure_skips_cache_update(cache_manager, test_cache_config):
    """API 응답이 실패(rt_cd != 0)면 캐시 저장 안 함"""
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    class FailClient:
        async def get_data(self, x):
            return ResCommonResponse(rt_cd="1", msg1="Error", data=None)

    wrapped = cache_wrap_client(
        api_client=FailClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config
    )

    key = "TEST_get_data_1"
    
    # 호출
    await wrapped.get_data(1)

    # 캐시에 저장되지 않았어야 함
    assert cache_manager.get_raw(key) is None
    
    # 로그 확인
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("응답 실패로" in msg and "Cache Update 무시" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_mode_unknown(cache_manager, test_cache_config):
    """mode_getter가 None을 반환할 때 unknown 모드로 동작"""
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    client = DummyApiClient()
    wrapped = cache_wrap_client(
        api_client=client,
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: None, # None 반환
        cache_manager=cache_manager,
        config=test_cache_config
    )

    await wrapped.get_data(1)

    # 키가 unknown_get_data_1 로 생성되었는지 확인 (캐시 매니저를 통해)
    # get_raw로 확인
    key = "unknown_get_data_1"
    loaded, _ = cache_manager.get_raw(key)
    assert loaded is not None
    assert loaded["data"].data["key"] == "value-1"

@pytest.mark.asyncio
async def test_cache_wrapper_with_market_date_manager_valid(cache_manager, test_cache_config):
    """MarketDateManager가 있고, 캐시 날짜가 최신 거래일과 같거나 이후면 유효"""
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False
    seoul_tz = pytz.timezone("Asia/Seoul")
    time_manager.market_timezone = seoul_tz
    
    # 현재 시간 설정 (장 마감 후)
    now = seoul_tz.localize(datetime(2025, 1, 1, 18, 0, 0))
    time_manager.get_current_kst_time.return_value = now
    # 다음 장 시작 시간 (내일 9시)
    time_manager.get_next_market_open_time.return_value = now + timedelta(hours=15)
    # 최근 장 종료 시간 (오늘 15:30)
    time_manager.get_latest_market_close_time.return_value = now - timedelta(hours=2.5)

    market_date_manager = AsyncMock()
    # 최근 거래일이 오늘(20250101)이라고 가정
    market_date_manager.get_latest_trading_date.return_value = "20250101"

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config,
        market_date_manager=market_date_manager
    )

    # 1. 캐시 생성 (오늘 날짜 데이터)
    key = "TEST_get_data_1"
    cache_data = {
        "timestamp": datetime(2025, 1, 1, 10, 0, 0).isoformat(), # 오늘 장중 데이터
        "data": ResCommonResponse(rt_cd="0", msg1="OK", data={"key": "cached"})
    }
    cache_manager.set(key, cache_data)

    # 2. 호출
    result = await wrapped.get_data(1)

    # 3. 검증: 캐시 HIT
    assert result.data["key"] == "cached"
    # API 호출 안함 (DummyApiClient는 value-1 반환함)
    market_date_manager.get_latest_trading_date.assert_awaited()
    
    # 로그 확인
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("Memory Cache HIT" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_cache_wrapper_with_market_date_manager_expired(cache_manager, test_cache_config):
    """MarketDateManager가 있고, 캐시 날짜가 최신 거래일보다 이전이면 만료"""
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False
    seoul_tz = pytz.timezone("Asia/Seoul")
    time_manager.market_timezone = seoul_tz
    
    # 현재 시간: 1월 2일 아침 (장 시작 전)
    now = seoul_tz.localize(datetime(2025, 1, 2, 8, 0, 0))
    time_manager.get_current_kst_time.return_value = now
    time_manager.get_next_market_open_time.return_value = now + timedelta(hours=1)
    # Mock get_latest_market_close_time to avoid TypeError in cache_wrapper
    time_manager.get_latest_market_close_time.return_value = now - timedelta(hours=16.5)
    
    market_date_manager = AsyncMock()
    # 최근 거래일이 어제(20250101)가 아니라 오늘(20250102)로 갱신되었다고 가정 (새벽에 갱신됨)
    market_date_manager.get_latest_trading_date.return_value = "20250102"

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config,
        market_date_manager=market_date_manager
    )

    # 1. 캐시 생성 (어제 날짜 데이터)
    key = "TEST_get_data_1"
    cache_data = {
        "timestamp": datetime(2025, 1, 1, 15, 0, 0).isoformat(), 
        "data": ResCommonResponse(rt_cd="0", msg1="OK", data={"key": "cached_old"})
    }
    cache_manager.set(key, cache_data)

    # 2. 호출
    result = await wrapped.get_data(1)

    # 3. 검증: 캐시 MISS (만료됨) -> API 호출 결과 반환
    assert result.data["key"] == "value-1" # DummyApiClient 반환값
    
    # 로그 확인
    debug_logs = [c.args[0] for c in logger.debug.call_args_list]
    assert any("캐시 만료" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_cache_wrapper_with_market_date_manager_fallback(cache_manager, test_cache_config):
    """MarketDateManager가 None을 반환하면 기존 시간 비교 로직 사용"""
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False
    seoul_tz = pytz.timezone("Asia/Seoul")
    time_manager.market_timezone = seoul_tz
    
    now = seoul_tz.localize(datetime(2025, 1, 1, 20, 0, 0))
    time_manager.get_current_kst_time.return_value = now
    time_manager.get_next_market_open_time.return_value = now + timedelta(hours=13)
    # 최근 장 종료: 오늘 15:30
    latest_close = seoul_tz.localize(datetime(2025, 1, 1, 15, 30, 0))
    time_manager.get_latest_market_close_time.return_value = latest_close

    market_date_manager = AsyncMock()
    market_date_manager.get_latest_trading_date.return_value = None # 날짜 확인 불가

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config,
        market_date_manager=market_date_manager
    )

    # 1. 캐시 생성 (장 종료 후 생성된 데이터 -> 유효해야 함)
    key = "TEST_get_data_1"
    cache_time = datetime(2025, 1, 1, 16, 0, 0) # 16:00 > 15:30
    cache_data = {
        "timestamp": cache_time.isoformat(),
        "data": ResCommonResponse(rt_cd="0", msg1="OK", data={"key": "cached_fallback"})
    }
    cache_manager.set(key, cache_data)

    # 2. 호출
    result = await wrapped.get_data(1)

    # 3. 검증: 캐시 HIT (fallback 로직: latest_close_time <= cache_time)
    assert result.data["key"] == "cached_fallback"
