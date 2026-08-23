import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from brokers.kiwoom.kiwoom_token_provider import KiwoomTokenProvider


@pytest.mark.asyncio
async def test_get_access_token_reuses_valid_file_token(tmp_path):
    token_file = tmp_path / "token.json"
    expires_at = datetime.now(pytz.timezone("Asia/Seoul")) + timedelta(hours=1)
    token_file.write_text(json.dumps({
        "access_token": "saved-token",
        "expires_dt": expires_at.isoformat(),
        "base_url": "https://mockapi.kiwoom.com",
    }), encoding="utf-8")

    provider = KiwoomTokenProvider(token_file_path=str(token_file))

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        token = await provider.get_access_token(
            base_url="https://mockapi.kiwoom.com",
            app_key="app-key",
            app_secret="secret-key",
        )

    assert token == "saved-token"
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_get_access_token_issues_and_saves_new_token(tmp_path):
    token_file = tmp_path / "token.json"
    provider = KiwoomTokenProvider(token_file_path=str(token_file))

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "token": "new-token",
        "token_type": "Bearer",
        "expires_dt": "20991231235959",
    }

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)

        token = await provider.get_access_token(
            base_url="https://mockapi.kiwoom.com",
            app_key="app-key",
            app_secret="secret-key",
        )

    assert token == "new-token"
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "new-token"
    assert saved["base_url"] == "https://mockapi.kiwoom.com"
    client_cls.return_value.__aenter__.return_value.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_issue_new_token_posts_official_token_body(tmp_path):
    provider = KiwoomTokenProvider(token_file_path=str(tmp_path / "token.json"))

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "access_token": "new-token",
        "expires_in": 3600,
    }

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        await provider._issue_new_token("https://api.kiwoom.com", "app-key", "secret-key")

    _, kwargs = client_cls.return_value.__aenter__.return_value.post.call_args
    assert kwargs["json"] == {
        "grant_type": "client_credentials",
        "appkey": "app-key",
        "secretkey": "secret-key",
    }
    assert kwargs["headers"]["Content-Type"] == "application/json;charset=UTF-8"


def _ok_response(payload):
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    return response


def test_default_token_path_is_used_when_none_given():
    provider = KiwoomTokenProvider()

    assert provider.token_file_path == "config/token_kiwoom.json"


@pytest.mark.asyncio
async def test_cached_valid_token_short_circuits_before_lock(tmp_path):
    provider = KiwoomTokenProvider(token_file_path=str(tmp_path / "token.json"))
    provider._access_token = "cached-token"
    provider._token_expired_at = datetime.now(pytz.timezone("Asia/Seoul")) + timedelta(hours=1)

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        token = await provider.get_access_token("https://api.kiwoom.com", "k", "s")

    assert token == "cached-token"
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_saved_token_for_other_base_url_is_reissued(tmp_path):
    token_file = tmp_path / "token.json"
    expires_at = datetime.now(pytz.timezone("Asia/Seoul")) + timedelta(hours=1)
    token_file.write_text(json.dumps({
        "access_token": "paper-token",
        "expires_dt": expires_at.isoformat(),
        "base_url": "https://mockapi.kiwoom.com",
    }), encoding="utf-8")
    provider = KiwoomTokenProvider(token_file_path=str(token_file))

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_ok_response({"access_token": "real-token", "expires_in": 3600})
        )
        token = await provider.get_access_token("https://api.kiwoom.com", "k", "s")

    assert token == "real-token"
    assert json.loads(token_file.read_text(encoding="utf-8"))["base_url"] == "https://api.kiwoom.com"


@pytest.mark.asyncio
async def test_expired_saved_token_is_reissued(tmp_path):
    token_file = tmp_path / "token.json"
    expired = datetime.now(pytz.timezone("Asia/Seoul")) - timedelta(hours=1)
    token_file.write_text(json.dumps({
        "token": "old-token",
        "expired_at": expired.isoformat(),
        "base_url": "https://api.kiwoom.com",
    }), encoding="utf-8")
    provider = KiwoomTokenProvider(token_file_path=str(token_file))

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_ok_response({"access_token": "new-token", "expires_in": 3600})
        )
        token = await provider.get_access_token("https://api.kiwoom.com", "k", "s")

    assert token == "new-token"


def test_load_token_from_file_clears_state_for_missing_or_broken_file(tmp_path):
    provider = KiwoomTokenProvider(token_file_path=str(tmp_path / "absent.json"))
    provider._access_token = "stale"
    provider._token_expired_at = datetime.now(pytz.timezone("Asia/Seoul"))

    provider._load_token_from_file()
    assert provider._access_token is None
    assert provider._token_expired_at is None
    assert provider._get_token_base_url_from_file() is None

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    provider.token_file_path = str(broken)
    provider._access_token = "stale"

    provider._load_token_from_file()
    assert provider._access_token is None
    assert provider._get_token_base_url_from_file() is None


def test_token_without_expiry_is_treated_as_invalid(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"access_token": "no-expiry"}), encoding="utf-8")
    provider = KiwoomTokenProvider(token_file_path=str(token_file))

    provider._load_token_from_file()

    assert provider._access_token == "no-expiry"
    assert provider._token_expired_at is None
    assert provider._is_token_valid() is False


@pytest.mark.parametrize(
    "value",
    [
        1800000000,
        "20991231235959",
        "2099-12-31T23:59:59",
        "2099-12-31T23:59:59+09:00",
    ],
)
def test_parse_expiry_supports_epoch_compact_and_iso_formats(value):
    provider = KiwoomTokenProvider(token_file_path="config/token_kiwoom.json")

    parsed = provider._parse_expiry(value)

    assert parsed.tzinfo is not None


@pytest.mark.asyncio
async def test_issue_new_token_requires_full_credentials(tmp_path):
    provider = KiwoomTokenProvider(token_file_path=str(tmp_path / "token.json"))

    with pytest.raises(ValueError, match="Missing environment configuration"):
        await provider._issue_new_token("", "app-key", "secret")
    with pytest.raises(ValueError, match="Missing environment configuration"):
        await provider._issue_new_token("https://api.kiwoom.com", "", "secret")
    with pytest.raises(ValueError, match="Missing environment configuration"):
        await provider._issue_new_token("https://api.kiwoom.com", "app-key", "")


@pytest.mark.asyncio
async def test_issue_new_token_rejects_response_without_token(tmp_path):
    provider = KiwoomTokenProvider(token_file_path=str(tmp_path / "token.json"))

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_ok_response({"token_type": "Bearer"})
        )
        with pytest.raises(ValueError, match="does not include access_token"):
            await provider._issue_new_token("https://api.kiwoom.com", "k", "s")


@pytest.mark.asyncio
async def test_issue_new_token_creates_parent_directory(tmp_path):
    token_file = tmp_path / "nested" / "dir" / "token.json"
    provider = KiwoomTokenProvider(token_file_path=str(token_file))

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_ok_response({"access_token": "new-token", "expires_in": 60})
        )
        await provider._issue_new_token("https://api.kiwoom.com", "k", "s")

    assert token_file.exists()


def test_invalidate_token_clears_state_and_removes_file(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")
    provider = KiwoomTokenProvider(token_file_path=str(token_file))
    provider._access_token = "token"
    provider._token_expired_at = datetime.now(pytz.timezone("Asia/Seoul"))

    provider.invalidate_token()

    assert provider._access_token is None
    assert provider._token_expired_at is None
    assert not token_file.exists()

    provider.invalidate_token()  # 파일이 없어도 안전해야 한다


@pytest.mark.asyncio
async def test_refresh_token_discards_saved_token_before_reissuing(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"access_token": "old"}), encoding="utf-8")
    provider = KiwoomTokenProvider(token_file_path=str(token_file))
    provider._access_token = "old"

    with patch("brokers.kiwoom.kiwoom_token_provider.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_ok_response({"access_token": "refreshed", "expires_dt": "20991231235959"})
        )
        await provider.refresh_token("https://api.kiwoom.com", "k", "s")

    assert provider._access_token == "refreshed"
    assert json.loads(token_file.read_text(encoding="utf-8"))["access_token"] == "refreshed"
