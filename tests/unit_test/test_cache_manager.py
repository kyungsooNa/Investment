# test_cache_manager.py

import pytest
import json
from core.cache.cache_manager import CacheManager
from datetime import datetime


@pytest.mark.asyncio
async def test_cache_manager_basic_set_get(cache_manager):
    key = "test_key"
    data = {"foo": 123}
    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": data
    })

    loaded, cache_type = cache_manager.get_raw(key)
    assert loaded['data'] == data
    assert cache_type == "memory"


@pytest.mark.asyncio
async def test_cache_manager_delete(cache_manager):
    key = "test_key"
    data = {"foo": "bar"}
    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": data
    })
    # 1. set
    loaded, cache_type = cache_manager.get_raw(key)
    assert loaded['data'] == data
    assert cache_type == "memory"

    # 2. delete
    cache_manager.delete(key)
    assert cache_manager.get_raw(key) is None


@pytest.mark.asyncio
async def test_cache_manager_delete_failure(cache_manager):
    key = "test_key"
    data = {"foo": "bar"}

    # 1. set
    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": data
    })

    loaded, cache_type = cache_manager.get_raw(key)
    assert loaded['data'] == data
    assert cache_type == "memory"

    key2 = "test_key2"

    # 2. delete
    cache_manager.delete(key2)
    assert cache_manager.get_raw(key) is not None


def test_cache_manager_set_creates_file(tmp_path):
    # ✅ base_dir을 tmp_path로 설정
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }
    cache_manager = CacheManager(config=config)

    key = "file_cache_test"
    data = {"x": 123}
    # Act

    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": data
    }, save_to_file=True)

    # Assert
    expected_file = tmp_path / f"{key}.json"
    assert expected_file.exists()


def test_cache_manager_file_cache_reuse(cache_manager, tmp_path):
    key = "file_reuse_test"
    data = {"y": 999}

    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": data
    }, save_to_file=True)

    # 메모리 클리어 (의도적으로)
    cache_manager.memory_cache.clear()

    # 캐시 재조회 시 파일에서 읽히는지 확인
    loaded, cache_type = cache_manager.get_raw(key)
    assert loaded['data'] == data
    assert cache_type == "file"

def test_cache_manager_file_cache_delete(tmp_path):
    # ✅ 캐시 설정 구성
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": [],
            "deserializable_classes": []
        }
    }
    cache_manager = CacheManager(config=config)

    key = "file_cache_delete_test"
    data = {"z": 42}

    # 1. 파일 캐시 저장
    cache_manager.set(key, {
        "timestamp": datetime.now().isoformat(),
        "data": data
    }, save_to_file=True)

    file_path = tmp_path / f"{key}.json"
    assert file_path.exists()

    # 2. 삭제 실행
    cache_manager.delete(key)

    # 3. 파일이 실제 삭제되었는지 확인
    assert not file_path.exists()


def test_cache_manager_file_cache_clear(tmp_path):
    # ✅ 설정: 임시 경로 사용
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": [],
            "deserializable_classes": []
        }
    }
    cache_manager = CacheManager(config=config)

    key1, key2 = "clear_test_1", "clear_test_2"
    data1, data2 = {"a": 1}, {"b": 2}

    # ✅ 캐시 저장
    cache_manager.set(key1, {
        "timestamp": datetime.now().isoformat(),
        "data": data1
    }, save_to_file=True)

    cache_manager.set(key2, {
        "timestamp": datetime.now().isoformat(),
        "data": data2
    },save_to_file=True)

    path1 = tmp_path / f"{key1}.json"
    path2 = tmp_path / f"{key2}.json"
    assert path1.exists()
    assert path2.exists()

    # ✅ 메모리 확인
    loaded, cache_type = cache_manager.get_raw(key1)
    assert loaded['data'] == data1
    assert cache_type == "memory"

    loaded, cache_type = cache_manager.get_raw(key2)
    assert loaded['data'] == data2
    assert cache_type == "memory"


    # ✅ 캐시 클리어
    cache_manager.clear()

    # ✅ 메모리 캐시 제거 확인
    assert cache_manager.get_raw(key1) is None
    assert cache_manager.get_raw(key2) is None

    # ✅ 파일 캐시 제거 확인
    assert not path1.exists()
    assert not path2.exists()
