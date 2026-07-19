"""현재가·재무·추세·수급·공시 컨텍스트를 이용한 종목 AI 분석."""
from __future__ import annotations

import json


_SYSTEM_PROMPT = (
    "너는 한국 주식 데이터를 해석하는 보조 애널리스트다. "
    "제공된 데이터에 근거한 사실과 해석을 구분하고, 데이터가 없으면 없다고 명시한다. "
    "매수·매도 같은 투자 권유나 확정적 예측은 하지 않는다. "
    "한국어로 '한줄 요약', '긍정 요인', '위험 요인', '기술·수급', "
    "'추가 확인사항' 순서의 짧은 섹션으로 답한다."
)


class AiStockAnalyzer:
    def __init__(self, ai_client, *, max_tokens: int = 2048) -> None:
        self._client = ai_client
        self._max_tokens = int(max_tokens)

    async def analyze(self, context: dict) -> str:
        normalized = {
            key: value if value not in (None, [], {}) else "데이터 없음"
            for key, value in context.items()
        }
        prompt = (
            "다음 종목 데이터를 분석해줘. 숫자의 단위와 기준일을 임의로 추정하지 말고, "
            "서로 충돌하는 데이터가 있으면 그 사실을 밝혀줘.\n\n"
            f"{json.dumps(normalized, ensure_ascii=False, default=str, indent=2)}"
        )
        result = await self._client.complete(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=self._max_tokens,
            temperature=0.2,
        )
        return str(result or "").strip()
