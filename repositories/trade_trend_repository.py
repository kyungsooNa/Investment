from __future__ import annotations

import json
from pathlib import Path
from typing import Union


class TradeTrendRepository:
    def __init__(self, path: Union[str, Path] = "data/trade_trend_state.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._sent_keys: set[str] = set()
        self._load()

    def has_sent(self, key: str) -> bool:
        return key in self._sent_keys

    def mark_sent(self, key: str) -> None:
        self._sent_keys.add(key)
        self._save()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._sent_keys = set(str(key) for key in payload.get("sent_keys", []))

    def _save(self) -> None:
        payload = {"sent_keys": sorted(self._sent_keys)}
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
