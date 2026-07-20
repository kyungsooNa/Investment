"""종목 뉴스 목록(제목·언론사·시각)을 이용한 AI 뉴스 검토."""
from __future__ import annotations

import json


_SYSTEM_PROMPT = (
    "너는 한국 주식 뉴스를 정리하는 보조 애널리스트다. "
    "입력으로는 기사 본문이 아니라 제목·언론사·게재 시각만 주어진다. "
    "제목에 없는 내용을 추측하지 말고, 확인이 필요하면 확인이 필요하다고 밝힌다. "
    "종목과 직접 관련 없는 시황·지수 기사는 노이즈로 분류한다. "
    "매수·매도 같은 투자 권유나 확정적 예측은 하지 않는다. "
    "응답 첫 줄에는 뉴스 제목들의 종합 톤을 '신호: 상'(호재 우세), "
    "'신호: 중'(중립·혼재), '신호: 하'(악재 우세) 중 하나로만 쓴다. "
    "판정 규칙: 노이즈로 분류한 기사는 판정에서 제외하고, 남은 제목에서 "
    "호재성과 악재성을 비교해 한쪽이 명백히 우세할 때만 상/하를 쓰고, "
    "비슷하거나 기사가 적으면 중으로 둔다. "
    "둘째 줄에는 '신호 근거: '로 시작해 판정을 가른 결정적 근거를 한 문장으로 쓴다. "
    "이 신호는 매매 지시가 아니라 제목 톤 분류다. "
    "그다음 줄부터 한국어로 '한줄 요약', '주요 이슈', '긍정 신호', '위험 신호', "
    "'노이즈로 판단한 기사' 순서의 짧은 섹션으로 답한다."
)


class AiNewsAnalyzer:
    def __init__(self, ai_client, *, max_tokens: int = 2048) -> None:
        self._client = ai_client
        self._max_tokens = int(max_tokens)

    async def analyze(self, context: dict) -> str:
        normalized = {
            key: value if value not in (None, [], {}) else "뉴스 없음"
            for key, value in context.items()
        }
        prompt = (
            "다음 종목의 최근 뉴스 목록을 검토해줘. 제목만 제공되므로 기사 본문 내용을 "
            "단정하지 말고, 같은 사안을 다룬 기사는 묶어서 정리해줘.\n\n"
            f"{json.dumps(normalized, ensure_ascii=False, default=str, indent=2)}"
        )
        result = await self._client.complete(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=self._max_tokens,
            temperature=0.2,
            usage_type="news",
        )
        return str(result or "").strip()
