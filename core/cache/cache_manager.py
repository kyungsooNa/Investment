# core/cache_manager.py

import os
import json
from typing import Any, Dict, Optional
from dataclasses import dataclass, field, fields, MISSING, asdict
from common.types import ResCommonResponse, ResStockFullInfoApiOutput
from core.time_manager import TimeManager
from datetime import datetime
from core.cache.cache_config import load_cache_config


class CacheManager:
    _instance: Optional['CacheManager'] = None
    _cache: Dict[str, Any] = {}
    _logger = None

    def __init__(self):
        config = load_cache_config()
        self._base_dir = config["cache"]["base_dir"]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CacheManager, cls).__new__(cls)
        return cls._instance

    def set_logger(self, logger):
        self._logger = logger

    def _serialize(self, value: Any) -> Any:
        """직렬화 불가능한 객체 (예: dataclass 인스턴스)를 처리"""
        if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
            # to_dict 메서드가 있는 객체는 해당 메서드를 사용하여 딕셔너리로 변환
            return value.to_dict()
        elif isinstance(value, (list, tuple)):
            # 리스트/튜플 내의 항목도 재귀적으로 직렬화
            return [self._serialize(item) for item in value]
        elif isinstance(value, dict):
            # 딕셔너리 내의 값도 재귀적으로 직렬화
            return {k: self._serialize(v) for k, v in value.items()}
        # 그 외 기본 JSON 직렬화 가능 타입은 그대로 반환
        return value

    def _deserialize(self, raw_data: Any) -> Any:
        """역직렬화 처리. 필요 시 dataclass 인스턴스로 복원"""
        if isinstance(raw_data, dict):
            # ResCommonResponse 구조일 경우 인스턴스로 복원 시도
            if "rt_cd" in raw_data and "msg1" in raw_data and "data" in raw_data:
                # data 필드를 재귀적으로 역직렬화
                deserialized_data_content = self._deserialize(raw_data["data"])
                return ResCommonResponse(
                    rt_cd=raw_data["rt_cd"],
                    msg1=raw_data["msg1"],
                    data=deserialized_data_content
                )
            # ResStockFullInfoApiOutput 구조일 경우 인스턴스로 복원 시도
            try:
                # 'MISSING'을 직접 참조하도록 수정
                if all(f.name in raw_data for f in fields(ResStockFullInfoApiOutput) if f.default is MISSING and f.default_factory is MISSING):
                    return ResStockFullInfoApiOutput.from_dict(raw_data)
            except TypeError:
                # from_dict 호출 시 타입 에러가 발생할 수 있으므로, 다른 딕셔너리로 처리
                pass

            # 일반 딕셔너리인 경우 내부 값도 역직렬화 시도
            return {k: self._deserialize(v) for k, v in raw_data.items()}
        elif isinstance(raw_data, (list, tuple)):
            return [self._deserialize(item) for item in raw_data]
        return raw_data # 기본 타입은 그대로 반환


    def set(self, key: str, value: Any, save_to_file: bool = False):
        self._cache[key] = value
        if save_to_file:
            try:
                path = self._base_dir + f"/{key}.json"
                os.makedirs(os.path.dirname(path), exist_ok=True)

                # _serialize 메서드를 사용하여 모든 객체를 직렬화 가능하도록 변환
                serialized_data = self._serialize(value)

                wrapper = {
                    "timestamp": datetime.now().isoformat(),
                    "data": serialized_data
                }


                with open(path, "w", encoding="utf-8") as f:
                    json.dump(wrapper, f, ensure_ascii=False, indent=2)
                if self._logger:
                    self._logger.debug(f"💾 File cache 저장: {path}")
            except Exception as e:
                if self._logger:
                    self._logger.error(f"❌ File cache 저장 실패: {e}")

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            if self._logger:
                self._logger.debug(f"✅ Memory cache HIT: {key}")
            return self._cache[key]

        if self._logger:
            self._logger.debug(f"🚫 Memory Cache MISS: {key}")

        path = self._base_dir + f"/{key}.json"
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    wrapper = json.load(f)
                    raw_data = wrapper.get("data")

                    # _deserialize 메서드를 사용하여 저장된 데이터를 객체로 복원
                    value = self._deserialize(raw_data)

                    self._cache[key] = value
                    if self._logger:
                        self._logger.debug(f"📂 File cache HIT: {key}")
                    return value
            except Exception as e:
                if self._logger:
                    self._logger.error(f"❌ File cache 로딩 실패: {e}")
        if self._logger:
            self._logger.debug(f"🚫 File Cache MISS: {key}")
        return None

    def delete(self, key: str):
        self._cache.pop(key, None)

        path = self._base_dir + f"/{key}.json"
        if os.path.exists(path):
            try:
                os.remove(path)
                if self._logger:
                    self._logger.debug(f"🗑️ File cache 삭제됨: {key}")
            except Exception as e:
                if self._logger:
                    self._logger.error(f"❌ File cache 삭제 실패: {e}")

    def clear(self):
        self._cache.clear()
        if self._logger:
            self._logger.debug("♻️ Memory cache 초기화 완료")


# 싱글톤 인스턴스
_cache_manager_instance = None

def get_cache_manager():
    global _cache_manager_instance
    if _cache_manager_instance is None:
        _cache_manager_instance = CacheManager()
    return _cache_manager_instance