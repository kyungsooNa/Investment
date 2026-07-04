from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from task.background.after_market.after_market_task_base import AfterMarketTask
from task.background.capture_candidates import resolve_capture_codes


_MIN_INTRADAY_COVERAGE_PCT = 80.0
_MIN_PROGRAM_OVERLAY_COVERAGE_PCT = 80.0
_MIN_PROGRAM_DB_COVERAGE_PCT = 50.0
_MAX_STALE_ROWS = 0


class MicrostructureCaptureTask(AfterMarketTask):
    """장마감 후 후보/보유 종목의 microstructure overlay를 캡처해 리플레이 코퍼스를 축적한다.

    todo 1-5: 체결강도·프로그램매매 수급 게이트는 장중 히스토리가 없어 백테스트
    검증이 불가능하다. 매 거래일 장마감 후 REST 경로로 당일 데이터를 수집해
    `data/backtest_microstructure/`에 replay fixture 규약 파일로 남긴다.
    """

    def __init__(
        self,
        capture_service,
        universe_service=None,
        virtual_trade_service=None,
        market_calendar_service=None,
        market_clock=None,
        scheduler_store=None,
        output_dir: str | Path = "data/backtest_microstructure",
        program_db_path: str | Path = "data/program_subscribe/program_trading.db",
        max_codes: int = 40,
        logger=None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=None,
        )
        self._service = capture_service
        self._universe_service = universe_service
        self._virtual_trade_service = virtual_trade_service
        self._output_dir = Path(output_dir)
        self._program_db_path = Path(program_db_path)
        self._max_codes = max_codes
        # 재시작 시 catch-up 중복 캡처 방지를 위해 "마지막 캡처 날짜"를 영속화한다.
        self._scheduler_store = scheduler_store
        self._state_key = "microstructure_capture_last_date"
        self._last_captured_date: Optional[str] = self._load_last_captured_date()
        self._progress = {
            "running": False,
            "last_captured_date": self._last_captured_date,
            "last_result": None,
        }

    @property
    def task_name(self) -> str:
        return "microstructure_capture"

    @property
    def _scheduler_label(self) -> str:
        return "MicrostructureCapture"

    # 시총갭 리포트(15:50)·기본 after-market 루프(15:41)와 클러스터링을 피하고
    # 당일 프로그램매매 daily REST 데이터가 정리된 뒤 실행한다.
    @property
    def _loop_cron_hour(self) -> int:
        return 16

    @property
    def _loop_cron_minute(self) -> int:
        return 5

    def get_progress(self) -> dict:
        return dict(self._progress)

    def _load_last_captured_date(self) -> Optional[str]:
        if self._scheduler_store is None:
            return None
        try:
            return self._scheduler_store.load_keyed(self._state_key)
        except Exception as exc:
            self._logger.warning(f"{self.task_name}: 마지막 캡처 날짜 로드 실패 — {exc}")
            return None

    def _save_last_captured_date(self, date_str: str) -> None:
        if self._scheduler_store is None:
            return
        try:
            self._scheduler_store.save_keyed(self._state_key, date_str)
        except Exception as exc:
            self._logger.warning(f"{self.task_name}: 마지막 캡처 날짜 저장 실패 — {exc}")

    async def _resolve_codes(self) -> list[str]:
        """캡처 대상 종목: 보유 종목 우선 + Pool B 워치리스트, 중복 제거 후 cap."""
        return await resolve_capture_codes(
            virtual_trade_service=self._virtual_trade_service,
            universe_service=self._universe_service,
            max_codes=self._max_codes,
            logger=self._logger,
            task_name=self.task_name,
        )

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if not latest_trading_date:
            return
        if self._last_captured_date == latest_trading_date:
            self._logger.info(
                f"{self.task_name}: {latest_trading_date} 이미 캡처됨 — skip"
            )
            return
        codes = await self._resolve_codes()
        if not codes:
            # 날짜를 저장하지 않아 이후 재실행(예: force) 시 재시도 여지를 남긴다.
            self._logger.warning(f"{self.task_name}: 캡처 대상 종목 없음 — skip")
            return
        program_source = (
            "program_db" if self._program_db_path.exists() else "daily_rest"
        )
        self._progress["running"] = True
        try:
            payload = await self._service.capture(
                codes=codes,
                date_ymd=latest_trading_date,
                program_source=program_source,
            )
            self._service.write_overlay_files(payload, self._output_dir)
            self._last_captured_date = latest_trading_date
            self._save_last_captured_date(latest_trading_date)
            self._progress["last_captured_date"] = latest_trading_date
            metadata = payload.get("metadata") or {}
            quality = metadata.get("quality") or {}
            program_fallback_codes = metadata.get("program_fallback_codes") or []
            quality_summary = _summarize_capture_quality(
                payload,
                fallback_codes=codes,
            )
            self._progress["last_result"] = {
                "codes": len(codes),
                "program_source": program_source,
                "row_counts": metadata.get("row_counts"),
                "program_fallback_codes": program_fallback_codes,
                "quality": quality,
                "quality_gate_passed": quality_summary["quality_gate_passed"],
                "quality_issues": quality_summary["issues"],
                "intraday_coverage_pct": quality_summary["intraday_coverage_pct"],
                "execution_strength_coverage_pct": quality_summary["execution_strength_coverage_pct"],
                "program_overlay_coverage_pct": quality_summary["program_overlay_coverage_pct"],
                "program_db_coverage_pct": quality_summary["program_db_coverage_pct"],
            }
            if not quality_summary["quality_gate_passed"]:
                self._logger.warning(
                    f"{self.task_name}: {latest_trading_date} 캡처 품질 게이트 실패 "
                    f"(issues={quality_summary['issues']}, "
                    f"intraday={quality_summary['intraday_coverage_pct']:.1f}%, "
                    f"program={quality_summary['program_overlay_coverage_pct']:.1f}%, "
                    f"program_db={_format_optional_pct(quality_summary['program_db_coverage_pct'])})"
                )
            self._logger.info(
                f"{self.task_name}: {latest_trading_date} 캡처 완료 "
                f"(codes={len(codes)}, program_source={program_source}, "
                f"program_fallback={len(program_fallback_codes)}, "
                f"empty_minutes={len(quality.get('empty_minute_codes') or [])})"
            )
        except Exception as exc:
            self._logger.error(f"{self.task_name}: 캡처 실패 — {exc}", exc_info=True)
            self._progress["last_result"] = {"error": str(exc)}
        finally:
            self._progress["running"] = False


def _summarize_capture_quality(
    payload: dict[str, Any],
    *,
    fallback_codes: list[str],
) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    codes = [str(code) for code in (metadata.get("codes") or fallback_codes or [])]
    code_set = set(codes)
    code_count = len(codes)
    intraday = payload.get("intraday_minutes") or {}
    execution_strength = payload.get("execution_strength") or {}
    program_trades = payload.get("program_trades") or {}
    quality = metadata.get("quality") or {}
    program_fallback_codes = [
        str(code) for code in (metadata.get("program_fallback_codes") or [])
        if str(code) in code_set
    ]
    stale_rows = sum(
        _to_int(value)
        for value in (quality.get("stale_minute_rows_dropped") or {}).values()
    )

    intraday_available = sum(1 for code in codes if bool(intraday.get(code)))
    execution_strength_available = sum(
        1 for code in codes if execution_strength.get(code) is not None
    )
    program_overlay_available = sum(
        1 for code in codes if program_trades.get(code) is not None
    )
    program_source = str(metadata.get("program_source") or "")
    program_db_coverage_pct = None
    if program_source == "program_db":
        program_db_available = max(0, program_overlay_available - len(program_fallback_codes))
        program_db_coverage_pct = _pct(program_db_available, code_count)

    intraday_coverage_pct = _pct(intraday_available, code_count)
    execution_strength_coverage_pct = _pct(execution_strength_available, code_count)
    program_overlay_coverage_pct = _pct(program_overlay_available, code_count)

    issues: list[str] = []
    if code_count == 0:
        issues.append("no_codes")
    if intraday_coverage_pct < _MIN_INTRADAY_COVERAGE_PCT:
        issues.append("intraday_coverage_below_threshold")
    if program_overlay_coverage_pct < _MIN_PROGRAM_OVERLAY_COVERAGE_PCT:
        issues.append("program_overlay_coverage_below_threshold")
    if (
        program_db_coverage_pct is not None
        and program_db_coverage_pct < _MIN_PROGRAM_DB_COVERAGE_PCT
    ):
        issues.append("program_db_coverage_below_threshold")
    if stale_rows > _MAX_STALE_ROWS:
        issues.append("stale_minute_rows_present")

    return {
        "quality_gate_passed": not issues,
        "issues": issues,
        "intraday_coverage_pct": intraday_coverage_pct,
        "execution_strength_coverage_pct": execution_strength_coverage_pct,
        "program_overlay_coverage_pct": program_overlay_coverage_pct,
        "program_db_coverage_pct": program_db_coverage_pct,
    }


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator * 100.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_optional_pct(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}%"
    return "-"
