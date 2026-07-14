"""전략 상세 진단 리포트 파일을 저장하고 조회한다."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Union


_REPORT_ID_PATTERN = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<microsecond>\d{6})_"
    r"strategy_diagnostic_report\.html$"
)


class StrategyDiagnosticReportRepository:
    """웹 상세 리포트 보관함의 파일 저장소."""

    def __init__(
        self,
        report_dir: Union[str, Path] = "logs/reports/strategy_diagnostics",
    ) -> None:
        self._report_dir = Path(report_dir)
        self._report_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _current_stamp() -> str:
        return datetime.now().strftime("%H%M%S_%f")

    def save(self, report_date: str, content: str) -> dict:
        safe_date = re.sub(r"[^0-9]", "", str(report_date))
        if len(safe_date) != 8:
            safe_date = datetime.now().strftime("%Y%m%d")
        report_id = (
            f"{safe_date}_{self._current_stamp()}_strategy_diagnostic_report.html"
        )
        path = self._report_dir / report_id
        path.write_text(content, encoding="utf-8")
        return self._metadata(report_id)

    def list_reports(self, limit: int = 100) -> list[dict]:
        report_ids = sorted(
            (
                path.name
                for path in self._report_dir.glob("*_strategy_diagnostic_report.html")
                if _REPORT_ID_PATTERN.fullmatch(path.name)
            ),
            reverse=True,
        )
        return [self._metadata(report_id) for report_id in report_ids[:limit]]

    def get_report(self, report_id: str) -> dict | None:
        if not _REPORT_ID_PATTERN.fullmatch(str(report_id)):
            return None
        path = self._report_dir / report_id
        if not path.is_file():
            return None
        return {
            **self._metadata(report_id),
            "content": path.read_text(encoding="utf-8"),
        }

    @staticmethod
    def _metadata(report_id: str) -> dict:
        match = _REPORT_ID_PATTERN.fullmatch(report_id)
        if match is None:  # pragma: no cover - 내부 검증 후에만 호출
            raise ValueError(f"잘못된 전략 진단 리포트 ID: {report_id}")
        created_at = datetime.strptime(
            f"{match.group('date')}{match.group('time')}{match.group('microsecond')}",
            "%Y%m%d%H%M%S%f",
        )
        return {
            "id": report_id,
            "report_date": match.group("date"),
            "created_at": created_at.isoformat(),
        }
