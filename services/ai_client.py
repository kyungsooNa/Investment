"""OpenAI 호환 chat completions 최소 비동기 클라이언트.

Gemini(생성형 AI OpenAI 호환 엔드포인트)·Groq·로컬 Ollama 를 동일 인터페이스로
호출한다. provider 차이는 base_url/api_key/model 설정으로 흡수하므로 코드는
provider 에 비의존한다.
"""
from __future__ import annotations

from typing import Optional

import httpx


class AiClientError(RuntimeError):
    def __init__(self, status: str, message: str):
        self.status = str(status or "")
        self.message = str(message or "AI API 오류")
        super().__init__(f"AI API 오류 ({self.status}): {self.message}")


class AiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 15.0,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._api_key = str(api_key or "")
        self._model = str(model or "")
        self._http_client = http_client
        self._timeout_sec = float(timeout_sec)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if self._http_client is not None:
            data = await self._request(self._http_client, url, headers, payload)
        else:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                data = await self._request(client, url, headers, payload)
        return self._parse(data)

    async def _request(self, client, url, headers, payload):
        response = await client.post(
            url, headers=headers, json=payload, timeout=self._timeout_sec
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse(payload: dict) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise AiClientError("EMPTY", "응답에 choices가 없습니다")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AiClientError("EMPTY", "응답 content가 비어 있습니다")
        return content.strip()
