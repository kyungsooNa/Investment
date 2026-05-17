"""RuntimeMode 단위 테스트.

- 환경변수 미설정 / 빈 문자열 → ALL
- 단일/조합 토큰 파싱
- 알 수 없는 토큰 → ALL fallback + WARN 로그
- `mode & RuntimeMode.X` 진리값 sanity
"""
import logging

import pytest

from view.web.bootstrap.runtime_mode import RuntimeMode


def test_from_env_defaults_to_all_when_missing():
    assert RuntimeMode.from_env(env={}) is RuntimeMode.ALL


def test_from_env_defaults_to_all_when_blank():
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": ""}) is RuntimeMode.ALL
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "   "}) is RuntimeMode.ALL


def test_from_env_parses_single_token():
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "WEB"}) is RuntimeMode.WEB
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "TRADING"}) is RuntimeMode.TRADING
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "BATCH"}) is RuntimeMode.BATCH
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "ALL"}) is RuntimeMode.ALL


def test_from_env_parses_pipe_combination():
    mode = RuntimeMode.from_env(env={"RUNTIME_MODE": "WEB|BATCH"})
    assert mode & RuntimeMode.WEB
    assert mode & RuntimeMode.BATCH
    assert not (mode & RuntimeMode.TRADING)


def test_from_env_parses_comma_combination():
    mode = RuntimeMode.from_env(env={"RUNTIME_MODE": "WEB,TRADING"})
    assert mode & RuntimeMode.WEB
    assert mode & RuntimeMode.TRADING
    assert not (mode & RuntimeMode.BATCH)


def test_from_env_case_insensitive():
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "web"}) is RuntimeMode.WEB
    assert RuntimeMode.from_env(env={"RUNTIME_MODE": "Web|Batch"}) == (
        RuntimeMode.WEB | RuntimeMode.BATCH
    )


def test_from_env_unknown_token_falls_back_to_all(caplog):
    with caplog.at_level(logging.WARNING, logger="view.web.bootstrap.runtime_mode"):
        result = RuntimeMode.from_env(env={"RUNTIME_MODE": "WEB|FOOBAR"})
    assert result is RuntimeMode.ALL
    assert any("unknown token" in r.message and "FOOBAR" in r.message for r in caplog.records)


def test_bitwise_truthiness_sanity():
    """`mode & RuntimeMode.X` 진리값이 의도대로 동작한다."""
    assert RuntimeMode.ALL & RuntimeMode.WEB
    assert RuntimeMode.ALL & RuntimeMode.TRADING
    assert RuntimeMode.ALL & RuntimeMode.BATCH
    assert not (RuntimeMode.BATCH & RuntimeMode.WEB)
    assert not (RuntimeMode.WEB & RuntimeMode.BATCH)
    assert (RuntimeMode.WEB | RuntimeMode.TRADING) & RuntimeMode.TRADING
    assert not ((RuntimeMode.WEB | RuntimeMode.TRADING) & RuntimeMode.BATCH)


def test_all_equals_or_of_components():
    assert RuntimeMode.ALL == (RuntimeMode.WEB | RuntimeMode.TRADING | RuntimeMode.BATCH)
