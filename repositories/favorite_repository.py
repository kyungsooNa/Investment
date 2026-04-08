"""
관심종목 저장소 - JSON 파일 기반 (data/favorites.json).
"""
import json
import threading
from datetime import datetime
from pathlib import Path


class FavoriteRepository:
    FILE_PATH = Path("data/favorites.json")

    def __init__(self):
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not self.FILE_PATH.exists():
            self._save([])

    def _load(self) -> list:
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("favorites", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, codes: list) -> None:
        with open(self.FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"favorites": codes, "updated_at": datetime.now().isoformat()},
                f,
                ensure_ascii=False,
                indent=2,
            )

    def get_all(self) -> list:
        with self._lock:
            return list(self._load())

    def add(self, code: str) -> bool:
        """종목 추가. 이미 존재하면 False 반환."""
        with self._lock:
            codes = self._load()
            if code in codes:
                return False
            codes.append(code)
            self._save(codes)
            return True

    def remove(self, code: str) -> bool:
        """종목 제거. 없으면 False 반환."""
        with self._lock:
            codes = self._load()
            if code not in codes:
                return False
            codes.remove(code)
            self._save(codes)
            return True

    def is_favorite(self, code: str) -> bool:
        with self._lock:
            return code in self._load()
