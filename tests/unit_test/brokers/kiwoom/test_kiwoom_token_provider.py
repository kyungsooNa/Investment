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
