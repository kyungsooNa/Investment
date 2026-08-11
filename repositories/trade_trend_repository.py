from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from services.trade_trend_service import NationalTradeTrendRelease


class TradeTrendRepository:
    def __init__(self, path: Union[str, Path] = "data/trade_trend_state.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._sent_keys: set[str] = set()
        self._national_release_history: list[dict] = []
        self._load()

    def has_sent(self, key: str) -> bool:
        return key in self._sent_keys

    def mark_sent(self, key: str) -> None:
        self._sent_keys.add(key)
        self._save()

    def mark_national_release_sent(
        self,
        release: NationalTradeTrendRelease,
        *,
        sent_at: str = "",
    ) -> None:
        self._sent_keys.add(release.dedup_key)
        row = _national_release_to_dict(release, sent_at=sent_at)
        existing = {
            str(item.get("dedup_key")): item
            for item in self._national_release_history
            if isinstance(item, dict)
        }
        existing[release.dedup_key] = row
        self._national_release_history = list(existing.values())
        self._save()

    def get_national_release_history(self) -> list[dict]:
        rows = list(self._national_release_history)
        known = {
            str(item.get("dedup_key"))
            for item in rows
            if isinstance(item, dict) and item.get("dedup_key")
        }
        known_periods = {
            (str(item.get("phase") or ""), str(item.get("period_label") or ""))
            for item in rows
            if isinstance(item, dict) and item.get("phase") and item.get("period_label")
        }
        for key in sorted(self._sent_keys):
            if not key.startswith("national_trade:") or key in known:
                continue
            legacy = _legacy_national_release_from_key(key)
            period_key = (
                str(legacy.get("phase") or ""),
                str(legacy.get("period_label") or ""),
            )
            if period_key in known_periods:
                continue
            rows.append(legacy)
            known_periods.add(period_key)
        return sorted(
            rows,
            key=lambda item: (
                str(item.get("published_at") or ""),
                str(item.get("period_label") or ""),
                str(item.get("sent_at") or ""),
            ),
            reverse=True,
        )

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._sent_keys = set(str(key) for key in payload.get("sent_keys", []))
        raw_history = payload.get("national_release_history", [])
        if isinstance(raw_history, list):
            self._national_release_history = [
                item for item in raw_history if isinstance(item, dict)
            ]

    def _save(self) -> None:
        payload = {
            "sent_keys": sorted(self._sent_keys),
            "national_release_history": self.get_national_release_history(),
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _national_release_to_dict(
    release: NationalTradeTrendRelease,
    *,
    sent_at: str = "",
) -> dict:
    return {
        "source": release.source,
        "source_type": "sent",
        "phase": release.phase,
        "title": release.title,
        "url": release.url,
        "period_label": release.period_label,
        "export_amount_100m_usd": release.export_amount_100m_usd,
        "export_yoy_pct": release.export_yoy_pct,
        "export_mom_change_100m_usd": release.export_mom_change_100m_usd,
        "export_mom_pct": release.export_mom_pct,
        "export_daily_avg_100m_usd": release.export_daily_avg_100m_usd,
        "export_daily_avg_mom_pct": release.export_daily_avg_mom_pct,
        "import_amount_100m_usd": release.import_amount_100m_usd,
        "import_yoy_pct": release.import_yoy_pct,
        "import_mom_change_100m_usd": release.import_mom_change_100m_usd,
        "import_mom_pct": release.import_mom_pct,
        "trade_balance_100m_usd": release.trade_balance_100m_usd,
        "trade_balance_label": release.trade_balance_label,
        "trade_balance_mom_change_100m_usd": release.trade_balance_mom_change_100m_usd,
        "semiconductor_export_amount_100m_usd": release.semiconductor_export_amount_100m_usd,
        "semiconductor_yoy_pct": release.semiconductor_yoy_pct,
        "semiconductor_mom_change_100m_usd": release.semiconductor_mom_change_100m_usd,
        "semiconductor_mom_pct": release.semiconductor_mom_pct,
        "semiconductor_daily_avg_100m_usd": release.semiconductor_daily_avg_100m_usd,
        "semiconductor_daily_avg_mom_pct": release.semiconductor_daily_avg_mom_pct,
        "working_days_current": release.working_days_current,
        "working_days_previous_year": release.working_days_previous_year,
        "published_at": release.published_at,
        "highlights": list(release.highlights or []),
        "sent_at": sent_at,
        "dedup_key": release.dedup_key,
    }


def _legacy_national_release_from_key(key: str) -> dict:
    _, phase, period_label, url = key.split(":", 3)
    return {
        "source": "",
        "source_type": "sent",
        "phase": phase,
        "title": "",
        "url": url,
        "period_label": period_label,
        "export_amount_100m_usd": None,
        "export_yoy_pct": None,
        "export_mom_change_100m_usd": None,
        "export_mom_pct": None,
        "export_daily_avg_100m_usd": None,
        "export_daily_avg_mom_pct": None,
        "import_amount_100m_usd": None,
        "import_yoy_pct": None,
        "import_mom_change_100m_usd": None,
        "import_mom_pct": None,
        "trade_balance_100m_usd": None,
        "trade_balance_label": "",
        "trade_balance_mom_change_100m_usd": None,
        "semiconductor_export_amount_100m_usd": None,
        "semiconductor_yoy_pct": None,
        "semiconductor_mom_change_100m_usd": None,
        "semiconductor_mom_pct": None,
        "semiconductor_daily_avg_100m_usd": None,
        "semiconductor_daily_avg_mom_pct": None,
        "working_days_current": None,
        "working_days_previous_year": None,
        "published_at": "",
        "highlights": [],
        "sent_at": "",
        "dedup_key": key,
    }
