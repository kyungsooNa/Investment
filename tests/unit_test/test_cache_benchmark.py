import time
import pytest
from core.cache.file_cache_manager import FileCacheManager
from core.cache.db_cache_manager import DBCacheManager

@pytest.fixture
def benchmark_data():
    # 적당한 크기의 데이터 생성 (약 4KB 정도)
    return {"key": "value", "data": [i for i in range(1000)]}

def run_benchmark(manager, operation, count, data=None):
    start_time = time.time()
    if operation == "set":
        for i in range(count):
            manager.set(f"key_{i}", data, save_to_file=True)
    elif operation == "get":
        for i in range(count):
            manager.get_raw(f"key_{i}")
    elif operation == "delete":
        for i in range(count):
            manager.delete(f"key_{i}")
    end_time = time.time()
    return end_time - start_time

def test_cache_performance_comparison(tmp_path, benchmark_data):
    """FileCacheManager vs DBCacheManager 성능 비교 벤치마크"""
    count = 500  # 테스트 항목 수
    
    # 1. FileCacheManager 설정
    file_cache_dir = tmp_path / "file_cache"
    file_config = {"cache": {"base_dir": str(file_cache_dir), "deserializable_classes": []}}
    file_manager = FileCacheManager(config=file_config)
    
    # 2. DBCacheManager 설정
    db_cache_dir = tmp_path / "db_cache"
    db_config = {"cache": {"base_dir": str(db_cache_dir), "deserializable_classes": []}}
    db_manager = DBCacheManager(config=db_config)
    
    print(f"\n\n[Cache Benchmark (N={count})]")
    
    # --- SET (Write) ---
    file_set_time = run_benchmark(file_manager, "set", count, benchmark_data)
    db_set_time = run_benchmark(db_manager, "set", count, benchmark_data)
    
    print(f"SET (Write):")
    print(f"  FileCache: {file_set_time:.4f}s")
    print(f"  DBCache:   {db_set_time:.4f}s")
    if db_set_time > 0:
        print(f"  Speedup:   {file_set_time / db_set_time:.2f}x")
    
    # --- GET (Read) ---
    file_get_time = run_benchmark(file_manager, "get", count)
    db_get_time = run_benchmark(db_manager, "get", count)
    
    print(f"GET (Read):")
    print(f"  FileCache: {file_get_time:.4f}s")
    print(f"  DBCache:   {db_get_time:.4f}s")
    if db_get_time > 0:
        print(f"  Speedup:   {file_get_time / db_get_time:.2f}x")
    
    # --- DELETE ---
    file_del_time = run_benchmark(file_manager, "delete", count)
    db_del_time = run_benchmark(db_manager, "delete", count)
    
    print(f"DELETE:")
    print(f"  FileCache: {file_del_time:.4f}s")
    print(f"  DBCache:   {db_del_time:.4f}s")
    if db_del_time > 0:
        print(f"  Speedup:   {file_del_time / db_del_time:.2f}x")