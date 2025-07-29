# core/cache_manager.py

import os
import json
from typing import Any, Dict, Optional

class CacheManager:
    _instance: Optional['CacheManager'] = None
    _cache: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CacheManager, cls).__new__(cls)
        return cls._instance

    def set(self, key: str, value: Any):
        self._cache[key] = value

        # # 📁 (@TODO) 파일 캐시에 저장
        # file_path = f"./cache/{key}.json"
        # os.makedirs(os.path.dirname(file_path), exist_ok=True)
        # with open(file_path, "w", encoding="utf-8") as f:
        #     json.dump(value, f, ensure_ascii=False, indent=2)

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            return self._cache[key]

        # # 📁 (@TODO) 파일에서 로드
        # file_path = f"./cache/{key}.json"
        # if os.path.exists(file_path):
        #     try:
        #         with open(file_path, "r", encoding="utf-8") as f:
        #             value = json.load(f)
        #             self._cache[key] = value  # 메모리에도 적재
        #             return value
        #     except Exception as e:
        #         print(f"파일 캐시 로딩 오류: {file_path} - {e}")

        return None

    def delete(self, key: str):
        if key in self._cache:
            del self._cache[key]

        # #@TODO 📁 파일도 삭제
        # file_path = f"./cache/{key}.json"
        # if os.path.exists(file_path):
        #     os.remove(file_path)

    def clear(self):
        self._cache.clear()

        # #@TODO 📁 파일 캐시 폴더도 비우기
        # cache_dir = "./cache"
        # if os.path.exists(cache_dir):
        #     for filename in os.listdir(cache_dir):
        #         file_path = os.path.join(cache_dir, filename)
        #         os.remove(file_path)


cache_manager = CacheManager()
