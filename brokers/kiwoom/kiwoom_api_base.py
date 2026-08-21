import json
import logging
import ssl
from typing import Optional

import certifi
import httpx

from common.types import ErrorCode, ResCommonResponse


class KiwoomApiBase:
    """키움 REST API 호출 공통 처리."""

    def __init__(self, env, logger=None, market_clock=None, async_client: Optional[httpx.AsyncClient] = None):
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)
        self.market_clock = market_clock
        if async_client:
            self._async_session = async_client
        else:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=30.0)
            timeout = httpx.Timeout(10.0, connect=5.0)
            self._async_session = httpx.AsyncClient(verify=ssl_context, limits=limits, timeout=timeout)

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self._env.get_base_url()}{path}"

    async def close_session(self):
        await self._async_session.aclose()

    async def call_api(self, method: str, path: str, *, api_id: str, params=None, data=None) -> ResCommonResponse:
        try:
            response = await self._execute_request(method, self.url(path), api_id, params=params, data=data)
            return self._handle_response(response)
        except httpx.RequestError as e:
            if self._logger:
                self._logger.error(f"키움 API 네트워크 오류: {e}")
            return ResCommonResponse(rt_cd=ErrorCode.NETWORK_ERROR.value, msg1=str(e), data=None)
        except Exception as e:
            if self._logger:
                self._logger.error(f"키움 API 호출 오류: {e}")
            return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1=str(e), data=None)

    async def _execute_request(self, method: str, url: str, api_id: str, *, params=None, data=None):
        token = await self._env.get_access_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
        }

        if method.upper() == "GET":
            return await self._async_session.get(url, headers=headers, params=params)
        if method.upper() == "POST":
            return await self._async_session.post(url, headers=headers, json=data or {})
        raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")

    def _handle_response(self, response) -> ResCommonResponse:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ResCommonResponse(
                rt_cd=ErrorCode.NETWORK_ERROR.value,
                msg1=f"HTTP Error {e.response.status_code}",
                data=None,
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1="JSON Decode Error", data=None)

        if not isinstance(payload, dict):
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1="Response is not a dict", data=None)

        return_code = payload.get("return_code")
        if return_code is not None and str(return_code) not in ("0", "0000"):
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=payload.get("return_msg") or payload.get("message") or "Kiwoom business error",
                data=None,
            )

        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=payload)
