"""여러 해외 dry-run 서비스를 한 번의 after-market 태스크에서 실행하는 합성 서비스.

한 전략이 죽어도 나머지는 계속 돌린다. 다만 삼킨 예외를 로그에만 남기면 "0건이라
신호가 없는 전략"과 "매일 죽어서 안 도는 전략"이 소비 측에서 구분되지 않으므로,
전략별 실행 결과를 `last_run_report` 로 함께 노출한다.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

from common.overseas_types import OverseasExchange


class OverseasDryRunSuiteService:
    def __init__(self, services: Iterable[Any], logger: Optional[logging.Logger] = None) -> None:
        self._services = list(services or [])
        self._logger = logger or logging.getLogger(__name__)
        self._last_run_report: List[dict] = []

    @property
    def last_run_report(self) -> List[dict]:
        """직전 `scan_dry_run` 의 전략별 실행 결과. 스캔마다 통째로 교체된다."""
        return list(self._last_run_report)

    async def scan_dry_run(self, exchange: OverseasExchange = OverseasExchange.NASD) -> List[dict]:
        signals: List[dict] = []
        report: List[dict] = []
        for service in self._services:
            strategy = getattr(service, "STRATEGY_NAME", service.__class__.__name__)
            try:
                result = await service.scan_dry_run(exchange)
            except Exception as e:
                self._logger.error({
                    "event": "overseas_dryrun_suite_service_error",
                    "service": service.__class__.__name__,
                    "strategy": strategy,
                    "exchange": exchange.value,
                    "error": str(e),
                }, exc_info=True)
                report.append({"strategy": strategy, "ok": False, "signals": 0, "error": str(e)})
                continue
            result = result or []
            report.append({"strategy": strategy, "ok": True, "signals": len(result), "error": None})
            signals.extend(result)
        self._last_run_report = report
        return signals
