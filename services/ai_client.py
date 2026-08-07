"""OpenAI 호환 chat completions 최소 비동기 클라이언트.

Gemini(생성형 AI OpenAI 호환 엔드포인트)·Groq·로컬 Ollama 를 동일 인터페이스로
호출한다. provider 차이는 base_url/api_key/model 설정으로 흡수하므로 코드는
provider 에 비의존한다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional
from urllib.parse import urlparse

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
        reasoning_effort: str = "",
        usage_limiter=None,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        max_concurrent_requests: int = 0,
        min_request_interval_sec: float = 0.0,
        max_input_chars: int = 0,
        logger=None,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        # 복사 과정에서 붙는 앞뒤 공백·개행 제거 (흔한 오염 원인)
        self._api_key = str(api_key or "").strip()
        self._model = str(model or "")
        self._http_client = http_client
        self._timeout_sec = float(timeout_sec)
        self._reasoning_effort = str(reasoning_effort or "").strip()
        self._usage_limiter = usage_limiter
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_sec = float(retry_backoff_sec)
        # 운영 하드닝(todo X-2). 기본값은 전부 비활성 — 정책은 조립 지점(config)이 정한다.
        limit = max(0, int(max_concurrent_requests))
        self._semaphore = asyncio.Semaphore(limit) if limit else None
        self._min_request_interval_sec = max(0.0, float(min_request_interval_sec))
        self._max_input_chars = max(0, int(max_input_chars))
        self._logger = logger
        self._pace_lock = asyncio.Lock()
        self._last_request_at: Optional[float] = None

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
        user = self._apply_input_budget(user)
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
        # thinking 모델(Gemini 2.5 등)은 사고 토큰도 max_tokens 예산을 쓰므로,
        # 제한하지 않으면 예산을 다 쓰고 본문이 잘린다. 미지원 provider 를 위해
        # 빈 값이면 파라미터 자체를 보내지 않는다.
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        if self._usage_limiter is not None:
            await self._usage_limiter.reserve(usage_type)
        return await self._dispatch(url, headers, payload, usage_type)

    async def _dispatch(self, url, headers, payload, usage_type: str) -> dict:
        """동시성·재호출 간격 제한을 적용해 요청을 내보내고 계측을 남긴다."""
        if self._semaphore is not None:
            async with self._semaphore:
                return await self._paced_request(url, headers, payload, usage_type)
        return await self._paced_request(url, headers, payload, usage_type)

    async def _paced_request(self, url, headers, payload, usage_type: str) -> dict:
        await self._wait_for_pace()
        started = time.monotonic()
        if self._http_client is not None:
            response_payload = await self._request(
                self._http_client, url, headers, payload
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                response_payload = await self._request(client, url, headers, payload)
        self._log_call(response_payload, usage_type, time.monotonic() - started)
        return response_payload

    async def _wait_for_pace(self) -> None:
        """직전 요청과의 최소 간격을 보장한다 (단시간 재호출 폭주 차단)."""
        if self._min_request_interval_sec <= 0:
            return
        async with self._pace_lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                wait = self._last_request_at + self._min_request_interval_sec - now
                if wait > 0:
                    await asyncio.sleep(wait)
                    now = time.monotonic()
            self._last_request_at = now

    def _apply_input_budget(self, user: str) -> str:
        """입력 컨텍스트 예산을 넘으면 뒤를 자른다.

        종합 분석은 재무·수급·공시·뉴스를 합치므로 소스가 늘수록 입력이 무한정
        커진다. 조용히 넘기면 토큰 비용만 늘고 응답은 오히려 잘린다.
        """
        if self._max_input_chars <= 0:
            return user
        text = str(user or "")
        if len(text) <= self._max_input_chars:
            return text
        marker = "\n…(입력 예산 초과로 이후 생략)"
        keep = max(0, self._max_input_chars - len(marker))
        if self._logger is not None:
            self._logger.warning(
                f"[AiClient] 입력 예산 초과 — {len(text)}자 → {self._max_input_chars}자로 절단"
            )
        return text[:keep] + marker

    def _log_call(self, payload: dict, usage_type: str, elapsed_sec: float) -> None:
        if self._logger is None:
            return
        usage = (payload or {}).get("usage") or {}
        choices = (payload or {}).get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        self._logger.info(
            f"[AiClient] usage_type={usage_type} model={self._model} "
            f"host={urlparse(self._base_url).netloc} "
            f"elapsed_ms={int(elapsed_sec * 1000)} "
            f"prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} "
            f"total_tokens={usage.get('total_tokens')} "
            f"finish_reason={finish_reason}"
        )

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
                response_payload = response.json()
                if self._is_aborted_completion(response_payload):
                    raise AiClientError(
                        "UPSTREAM_ABORTED",
                        "AI 공급자 응답이 일시적으로 중단되었습니다. 잠시 후 다시 시도하세요.",
                    )
                return response_payload
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    raise
                last_exc = exc
            except httpx.TimeoutException as exc:
                # 같은 제한 시간으로 다시 걸면 결과도 같으므로 재시도하지 않는다.
                # (재시도하면 사용자 대기만 제한 시간의 3배로 늘어난다.)
                raise AiClientError(
                    "TIMEOUT",
                    f"AI 응답이 {self._timeout_sec:.0f}초 안에 도착하지 않았습니다. "
                    "잠시 후 다시 시도하거나 ai_analysis.timeout_sec 를 늘리세요.",
                ) from exc
            except httpx.TransportError as exc:
                last_exc = exc
            except AiClientError as exc:
                if exc.status != "UPSTREAM_ABORTED":
                    raise
                last_exc = exc
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff_sec * (2**attempt))
        raise last_exc

    @staticmethod
    def _is_aborted_completion(payload: dict) -> bool:
        """Gemini OpenAI 호환 API의 HTTP 200 내부 중단 응답을 감지한다."""
        choices = payload.get("choices") or []
        if not choices:
            return False
        message = choices[0].get("message") or {}
        content = message.get("content")
        return (
            isinstance(content, str)
            and "signal is aborted without reason" in content.casefold()
        )

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
