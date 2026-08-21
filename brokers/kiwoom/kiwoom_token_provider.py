import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pytz


class KiwoomTokenProvider:
    """키움 REST API 접근 토큰을 파일에 저장하고 재사용합니다."""

    def __init__(self, token_file_path: Optional[str] = None, logger=None):
        self.token_file_path = str(token_file_path) if token_file_path else "config/token_kiwoom.json"
        self._access_token = None
        self._token_expired_at = None
        self._logger = logger if logger else logging.getLogger(__name__)
        self._issue_lock = asyncio.Lock()

    async def get_access_token(self, base_url: str, app_key: str, app_secret: str) -> str:
        if self._access_token and self._is_token_valid():
            return self._access_token

        async with self._issue_lock:
            if self._access_token and self._is_token_valid():
                return self._access_token

            self._load_token_from_file()
            if self._access_token and self._is_token_valid() and self._get_token_base_url_from_file() == base_url:
                return self._access_token

            await self._issue_new_token(base_url, app_key, app_secret)
            return self._access_token

    def _is_token_valid(self) -> bool:
        if not self._access_token or not self._token_expired_at:
            return False
        now_kst = datetime.now(pytz.timezone("Asia/Seoul"))
        return now_kst < self._token_expired_at - timedelta(minutes=5)

    def _load_token_from_file(self):
        try:
            with open(self.token_file_path, "r", encoding="utf-8") as f:
                token_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._access_token = None
            self._token_expired_at = None
            return

        self._access_token = token_data.get("access_token") or token_data.get("token")
        expiry = token_data.get("expires_dt") or token_data.get("expired_at")
        self._token_expired_at = self._parse_expiry(expiry) if expiry else None

    def _get_token_base_url_from_file(self):
        try:
            with open(self.token_file_path, "r", encoding="utf-8") as f:
                return json.load(f).get("base_url")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _parse_expiry(self, value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=pytz.timezone("Asia/Seoul"))
        text = str(value)
        kst = pytz.timezone("Asia/Seoul")
        if len(text) == 14 and text.isdigit():
            return kst.localize(datetime.strptime(text, "%Y%m%d%H%M%S"))
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return kst.localize(parsed)
        return parsed

    def _save_token_to_file(self, base_url: str):
        token_data = {
            "access_token": self._access_token,
            "expires_dt": self._token_expired_at.isoformat(),
            "base_url": base_url,
        }
        parent = os.path.dirname(self.token_file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.token_file_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=4, ensure_ascii=False)

    async def _issue_new_token(self, base_url: str, app_key: str, app_secret: str):
        if not base_url or not app_key or not app_secret:
            raise ValueError("Missing environment configuration for Kiwoom token issuance.")

        url = f"{base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        }
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        self._access_token = data.get("access_token") or data.get("token")
        if not self._access_token:
            raise ValueError("Kiwoom token response does not include access_token/token.")

        expiry = data.get("expires_dt") or data.get("expired_at")
        if expiry:
            self._token_expired_at = self._parse_expiry(expiry)
        else:
            expires_in = int(data.get("expires_in", 86400))
            self._token_expired_at = datetime.now(pytz.timezone("Asia/Seoul")) + timedelta(seconds=expires_in)

        self._save_token_to_file(base_url)

    def invalidate_token(self):
        self._access_token = None
        self._token_expired_at = None
        if os.path.exists(self.token_file_path):
            os.remove(self.token_file_path)

    async def refresh_token(self, base_url: str, app_key: str, app_secret: str):
        self.invalidate_token()
        await self._issue_new_token(base_url, app_key, app_secret)
