from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from task.background.after_market.after_market_task_base import AfterMarketTask
from task.background.capture_candidates import resolve_capture_codes


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
            self._progress["last_result"] = {
                "codes": len(codes),
                "program_source": program_source,
                "row_counts": metadata.get("row_counts"),
                "program_fallback_codes": program_fallback_codes,
                "quality": quality,
            }
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
