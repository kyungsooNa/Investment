# core/cache/file_cache_manager.py

import os
import json
import importlib
from typing import Optional, Any
from dataclasses import dataclass, field, fields, MISSING, asdict
from datetime import datetime
from core.cache.cache_config import load_cache_config

def load_deserializable_classes(class_paths: list[str]) -> list[type]:
    classes = []
    for path in class_paths:
        try:
            module_path, class_name = path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            classes.append(cls)
        except Exception as e:
            print(f"[❌ 클래스 로드 실패] {path}: {e}")
    return classes

class FileCacheManager:
    def __init__(self, config: Optional[dict] = None):
        if config is None:
            config = load_cache_config()
        self._base_dir = config["cache"]["base_dir"]
        self._logger = None
        self._deserializable_classes = load_deserializable_classes(config["cache"].get("deserializable_classes", []))

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
        if isinstance(raw_data, dict):
            for cls in self._deserializable_classes:
                try:
                    cls_fields = {f.name for f in fields(cls)}
                    if cls_fields.issubset(raw_data.keys()):
                        if cls.__name__ == "ResCommonResponse" and "data" in raw_data:
                            # ✅ 내부 data도 재귀 복원
                            raw_data["data"] = self._deserialize(raw_data["data"])
                        return cls.from_dict(raw_data)
                except Exception as e:
                    ...
            return {k: self._deserialize(v) for k, v in raw_data.items()}

        elif isinstance(raw_data, (list, tuple)):
            return [self._deserialize(item) for item in raw_data]

        return raw_data

    def _get_path(self, key: str):
        return os.path.join(self._base_dir, f"{key}.json")

    def get(self, key: str):
        path = self._get_path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                wrapper = json.load(f)
                raw_data = wrapper.get("data")

                # _deserialize 메서드를 사용하여 저장된 데이터를 객체로 복원
                value = self._deserialize(raw_data)
                return value

        except Exception as e:
            if self._logger:
                self._logger.error(f"[FileCache] Load Error: {e}")
        return None

    def set(self, key: str, value: Any, save_to_file: bool = False):
        if save_to_file:
            try:
                path = self._base_dir + f"/{key}.json"
                os.makedirs(os.path.dirname(path), exist_ok=True)

                # _serialize 메서드를 사용하여 모든 객체를 직렬화 가능하도록 변환
                serialized_data = self._serialize(value)

                # wrapper = {
                #     "timestamp": datetime.now().isoformat(),
                #     "data": serialized_data
                # }
                wrapper = serialized_data

                with open(path, "w", encoding="utf-8") as f:
                    json.dump(wrapper, f, ensure_ascii=False, indent=2)
                if self._logger:
                    self._logger.debug(f"💾 File cache 저장: {path}")
            except Exception as e:
                if self._logger:
                    self._logger.error(f"❌ File cache 저장 실패: {e}")

    def delete(self, key: str):
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
        """파일 캐시 전체 삭제"""
        if not os.path.exists(self._base_dir):
            return

        try:
            for root, _, files in os.walk(self._base_dir):
                for file in files:
                    if file.endswith(".json"):
                        path = os.path.join(root, file)
                        try:
                            os.remove(path)
                            if self._logger:
                                self._logger.debug(f"🗑️ File cache 삭제됨: {path}")
                        except Exception as e:
                            if self._logger:
                                self._logger.error(f"❌ 파일 삭제 실패: {path} - {e}")
        except Exception as e:
            if self._logger:
                self._logger.error(f"❌ 전체 캐시 삭제 실패: {e}")

    def get_raw(self, key: str):
        path = self._get_path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                wrapper = json.load(f)
                data = wrapper["data"]
                wrapper['data'] = self._deserialize(data)

                return wrapper

        except Exception as e:
            if self._logger:
                self._logger.error(f"[FileCache] Load Error: {e}")
        return None

    def set_raw(self, key: str, wrapper: dict):
        path = self._get_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wrapper, f, ensure_ascii=False, indent=2)

    def exists(self, key: str) -> bool:
        """파일 캐시 존재 여부 확인"""
        return os.path.exists(self._get_path(key))
