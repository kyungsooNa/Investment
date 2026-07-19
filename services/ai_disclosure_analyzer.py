"""공시 제목/메타 기반 AI 요약 — 실패 시 None 반환(규칙 판정으로 폴백).

OpenDART list API 가 제공하는 제목·기업명·중요도만으로 투자자 관점 핵심을
요약한다. 원문 다운로드는 하지 않는다(경량 1차). AI 호출이 실패/타임아웃/빈
응답이면 None 을 돌려 호출측이 기존 규칙 판정을 그대로 쓰도록 한다.
"""
from __future__ import annotations

import logging
from typing import Optional

from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DisclosureImportance


_SYSTEM_PROMPT = (
    "너는 한국 주식 투자자를 돕는 애널리스트다. "
    "기업 공시의 제목과 메타데이터를 보고, 투자 판단에 중요한 핵심을 한국어로 "
    "2~3문장으로 간결하게 요약한다. 제목에 근거한 사실만 전달하고 과장·추측은 피한다."
)


class AiDisclosureAnalyzer:
    def __init__(self, ai_client, *, logger=None, max_tokens: int = 2048):
        self._client = ai_client
        self._logger = logger or logging.getLogger(__name__)
        self._max_tokens = int(max_tokens)

    async def summarize(
        self, disclosure: DartDisclosure, importance: DisclosureImportance
    ) -> Optional[str]:
        try:
            summary = await self._client.complete(
                system=_SYSTEM_PROMPT,
                user=self._build_prompt(disclosure, importance),
                max_tokens=self._max_tokens,
                usage_type="disclosure",
            )
        except Exception as exc:
            self._logger.warning(
                f"AI 공시 요약 실패 — 규칙 판정으로 폴백: {type(exc).__name__}: {exc}"
            )
            return None
        summary = str(summary or "").strip()
        return summary or None

    @staticmethod
    def _build_prompt(
        disclosure: DartDisclosure, importance: DisclosureImportance
    ) -> str:
        reasons = ", ".join(importance.reasons) if importance.reasons else "-"
        return (
            f"기업명: {disclosure.corp_name} ({disclosure.stock_code})\n"
            f"공시 제목: {disclosure.report_name}\n"
            f"제출인: {disclosure.filer_name}\n"
            f"접수일: {disclosure.receipt_date}\n"
            f"비고: {disclosure.remarks or '-'}\n"
            f"규칙 판정: {importance.level} ({importance.score}점) — {reasons}\n\n"
            "위 공시의 핵심을 투자자 관점에서 2~3문장으로 요약해줘."
        )
