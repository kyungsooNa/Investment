"""Runtime-specific entrypoint helpers.

The runtime split is currently implemented by setting ``RUNTIME_MODE`` before
delegating to the existing FastAPI bootstrap. Each thin top-level entrypoint
uses these helpers so process managers can launch WEB/TRADING/BATCH/ADMIN with
an explicit mode while the shared startup path remains unchanged.
"""
from __future__ import annotations

import os
from typing import Final

from view.web.bootstrap.runtime_mode import RuntimeMode

RUNTIME_MODE_ENV: Final[str] = "RUNTIME_MODE"


def configure_runtime(mode: RuntimeMode | str) -> str:
    """Set ``RUNTIME_MODE`` and return the normalized token."""
    token = mode.name if isinstance(mode, RuntimeMode) else str(mode).strip().upper()
    os.environ[RUNTIME_MODE_ENV] = token
    return token


def run_mode(mode: RuntimeMode | str) -> None:
    """Run the existing web bootstrap under a specific runtime mode."""
    configure_runtime(mode)
    from main import run_web

    run_web()


def run_web_runtime() -> None:
    run_mode(RuntimeMode.WEB)


def run_trading_runtime() -> None:
    run_mode(RuntimeMode.TRADING)


def run_batch_runtime() -> None:
    run_mode(RuntimeMode.BATCH)


def run_admin_runtime() -> None:
    # Admin/manual operations currently use the WEB surface with trading/batch
    # schedulers disabled. Keeping this as a separate entrypoint makes the
    # eventual read-only admin policy an isolated change.
    run_mode(RuntimeMode.WEB)
