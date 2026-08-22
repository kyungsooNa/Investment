"""여러 해외 dry-run 서비스를 한 번의 after-market 태스크에서 실행하는 합성 서비스."""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

from common.overseas_types import OverseasExchange


class OverseasDryRunSuiteService:
    def __init__(self, services: Iterable[Any], logger: Optional[logging.Logger] = None) -> None:
        self._services = list(services or [])
        self._logger = logger or logging.getLogger(__name__)

    async def scan_dry_run(self, exchange: OverseasExchange = OverseasExchange.NASD) -> List[dict]:
        signals: List[dict] = []
        for service in self._services:
            try:
                result = await service.scan_dry_run(exchange)
            except Exception as e:
                self._logger.error({
                    "event": "overseas_dryrun_suite_service_error",
                    "service": service.__class__.__name__,
                    "exchange": exchange.value,
                    "error": str(e),
                }, exc_info=True)
                continue
            signals.extend(result or [])
        return signals
