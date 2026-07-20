"""OpenAI 호환 chat completions 최소 비동기 클라이언트.

Gemini(생성형 AI OpenAI 호환 엔드포인트)·Groq·로컬 Ollama 를 동일 인터페이스로
호출한다. provider 차이는 base_url/api_key/model 설정으로 흡수하므로 코드는
provider 에 비의존한다.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx

# 업스트림 과부하/속도제한 등 재시도로 회복 가능한 상태 코드
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


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
        usage_limiter=None,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        # 복사 과정에서 붙는 앞뒤 공백·개행 제거 (흔한 오염 원인)
        self._api_key = str(api_key or "").strip()
        self._model = str(model or "")
        self._http_client = http_client
        self._timeout_sec = float(timeout_sec)
        self._usage_limiter = usage_limiter
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_sec = float(retry_backoff_sec)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        usage_type: str = "general",
    ) -> str:
        data = await self.chat_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            usage_type=usage_type,
        )
        return self._parse(data)

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        usage_type: str = "general",
    ) -> dict:
        """파싱 전 원본 응답 JSON을 반환한다 (진단용: finish_reason/usage 확인)."""
        url = f"{self._base_url}/chat/completions"
        headers = {}
        if self._api_key:
            if not self._api_key.isascii():
                raise AiClientError(
                    "KEY_ENCODING",
                    "API 키에 ASCII 외 문자가 섞여 있습니다 — config.yaml의 키를 "
                    "지우고 공백·한글 없이 다시 붙여넣으세요.",
                )
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
        if self._usage_limiter is not None:
            await self._usage_limiter.reserve(usage_type)
        if self._http_client is not None:
            return await self._request(self._http_client, url, headers, payload)
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            return await self._request(client, url, headers, payload)

    async def _request(self, client, url, headers, payload):
        """일시적 업스트림 오류(503 과부하 등)는 지수 백오프로 재시도한다.

        사용량 예약은 호출 전 1회만 하므로 재시도가 일일 할당량을 추가로 쓰지 않는다.
        """
        last_exc = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(
                    url, headers=headers, json=payload, timeout=self._timeout_sec
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    raise
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff_sec * (2**attempt))
        raise last_exc

    @staticmethod
    def _parse(payload: dict) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise AiClientError("EMPTY", "응답에 choices가 없습니다")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AiClientError("EMPTY", "응답 content가 비어 있습니다")
        text = content.strip()
        # thinking 모델(Gemini 2.5 등)은 thinking 토큰이 max_tokens 를 소비해
        # 본문이 문장 중간에서 잘릴 수 있다. 조용히 넘기지 않고 명시한다.
        if choices[0].get("finish_reason") == "length":
            text += (
                "\n\n⚠️ 응답이 max_tokens 한도로 잘려 마지막 부분이 누락되었습니다. "
                "config.yaml의 ai_analysis.max_tokens 상향이 필요합니다."
            )
        return text
