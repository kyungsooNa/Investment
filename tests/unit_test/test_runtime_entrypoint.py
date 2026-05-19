import importlib
import os
from unittest.mock import patch

from runtime_entrypoint import (
    RUNTIME_MODE_ENV,
    configure_runtime,
    run_admin_runtime,
    run_batch_runtime,
    run_mode,
    run_trading_runtime,
    run_web_runtime,
)
from view.web.bootstrap.runtime_mode import RuntimeMode


def test_configure_runtime_sets_env(monkeypatch):
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)

    token = configure_runtime(RuntimeMode.TRADING)

    assert token == "TRADING"
    assert os.environ[RUNTIME_MODE_ENV] == "TRADING"


def test_run_mode_sets_env_before_delegating(monkeypatch):
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)

    with patch("main.run_web") as run_web:
        run_mode(RuntimeMode.BATCH)

    assert os.environ[RUNTIME_MODE_ENV] == "BATCH"
    run_web.assert_called_once_with()


def test_named_runtime_helpers_delegate_to_expected_modes(monkeypatch):
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)

    with patch("main.run_web") as run_web:
        run_web_runtime()
        assert os.environ[RUNTIME_MODE_ENV] == "WEB"
        run_trading_runtime()
        assert os.environ[RUNTIME_MODE_ENV] == "TRADING"
        run_batch_runtime()
        assert os.environ[RUNTIME_MODE_ENV] == "BATCH"
        run_admin_runtime()
        assert os.environ[RUNTIME_MODE_ENV] == "WEB"

    assert run_web.call_count == 4


def test_top_level_runtime_modules_import_cleanly():
    for module_name in ["web_app", "trading_runtime", "batch_runtime", "admin_runtime"]:
        importlib.import_module(module_name)
