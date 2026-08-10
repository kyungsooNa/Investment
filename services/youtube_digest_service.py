"""유튜브 방송 자막을 종목 언급 집계 + AI 요약으로 묶어 일일 리포트를 만드는 서비스.

설계 의도 두 가지:

1. **종목 언급 집계는 AI를 쓰지 않는다.** 자막에서 종목명을 직접 세면 검증 가능하고
   환각이 없다. AI는 "무슨 맥락으로 말했는지"만 담당한다.
2. **집계는 최장일치 + 좌측 경계 스캔이다.** 2026-08-10 실데이터에서 확인한 오검출을
   막기 위한 3중 규칙이다.
   - 최장일치: `SK하이닉스` 안의 `SK` 를 따로 세지 않는다.
   - 좌측 경계: 매칭 직전 글자가 낱말 글자면 버린다. 공백을 지우고 매칭하면
     `하이닉스`→`이닉스`, `그레이드`→`레이`, `에서 한`→`서한` 처럼 어절 내부가
     걸린다(실측: 레이 59회·이닉스 13회 전량 오검출).
   - 최소 길이 3: 2글자 종목명(레이·서한·고영·테스…)은 음성인식 자막에서
     오검출이 압도적이라 아예 후보에서 뺀다.

리포트는 유튜버의 **발언 집계**이지 사실 확인이나 매매 판단이 아니다. 프롬프트도
그 선을 넘지 않도록 제약한다.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Mapping, Optional, Sequence

from common.types import ErrorCode, ResCommonResponse

# 상장 종목명이지만 일반명사로 훨씬 자주 쓰여 카운트가 무의미해지는 이름.
# 최장일치·좌측경계로도 걸러지지 않으므로 명시적으로 제외한다.
_AMBIGUOUS_NAMES = frozenset(
    {"이번주", "이스트", "네이처", "코리아", "하이라이트", "포인트", "제이스"}
)

# 최장일치 스캔에서 볼 최대 이름 길이. 실제 종목명은 이보다 짧다.
_MAX_NAME_LEN = 20

# 2글자 이름은 자막에서 어절 파편과 충돌해 오검출이 압도적이라 후보에서 뺀다.
_MIN_NAME_LEN = 3

_RE_SPACE = re.compile(r"\s+")

_VIDEO_SYSTEM_PROMPT = (
    "너는 한국 주식 유튜브 방송의 자동생성 자막을 정리하는 보조자다. "
    "자막은 음성인식 결과라 종목명·숫자에 오탈자가 섞여 있다. "
    "확실하지 않은 고유명사는 단정하지 말고 '불확실'로 표시한다. "
    "자막에 없는 내용을 채워 넣지 않는다. "
    "이것은 출연자의 발언 정리이지 사실 확인이나 투자 판단이 아니다. "
    "매수·매도 지시나 목표가 제시를 하지 않고, 출연자가 그런 말을 했다면 "
    "'출연자 발언'임을 명시해 인용만 한다. "
    "한국어로 '핵심 주장', '언급 종목과 이유', '시장 전망', '리스크 언급' "
    "순서의 짧은 불릿으로 답한다."
)

_DIGEST_SYSTEM_PROMPT = (
    "너는 여러 주식 유튜브 방송의 하루치 요약을 묶어 일일 다이제스트를 만드는 보조자다. "
    "여러 채널이 공통으로 말한 주제를 위로 올리고, 한 채널만 말한 내용은 "
    "'단독 언급'으로 따로 묶는다. "
    "채널마다 의견이 엇갈리면 어느 쪽이 맞다고 판정하지 말고 양쪽을 나란히 적는다. "
    "입력으로 주어진 종목 언급 횟수는 자막에서 기계적으로 센 값이다. "
    "횟수가 많다고 유망하다는 뜻이 아니므로 그렇게 해석하지 않는다. "
    "이것은 발언 집계이지 투자 권유가 아니다. 매수·매도 지시를 하지 않는다. "
    "한국어로 '오늘의 공통 화두', '많이 언급된 종목', '엇갈리는 시각', "
    "'단독 언급', '확인이 필요한 주장' 순서의 짧은 섹션으로 답한다."
)


class YoutubeDigestService:
    """자막 묶음 → 종목 언급 집계 + AI 다이제스트."""

    def __init__(
        self,
        ai_client,
        stock_code_repository=None,
        *,
        max_tokens: int = 2048,
        chunk_chars: int = 18000,
        max_mentions: int = 30,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._client = ai_client
        self._code_repo = stock_code_repository
        self._max_tokens = int(max_tokens)
        self._chunk_chars = max(1, int(chunk_chars))
        self._max_mentions = max(1, int(max_mentions))
        self._logger = logger or logging.getLogger(__name__)
        self._name_index: Optional[dict] = None

    # --- 종목 언급 집계 (AI 미사용) --------------------------------------------

    def _build_name_index(self) -> dict:
        """공백 제거한 종목명 → 종목코드. 최초 1회만 만든다."""
        if self._name_index is not None:
            return self._name_index
        index: dict = {}
        raw = getattr(self._code_repo, "name_to_code", None) or {}
        for name, code in raw.items():
            key = _RE_SPACE.sub("", str(name or ""))
            if len(key) < _MIN_NAME_LEN or len(key) > _MAX_NAME_LEN:
                continue
            if key in _AMBIGUOUS_NAMES or key.isdigit():
                continue
            index[key] = str(code)
        self._name_index = index
        return index

    @staticmethod
    def _compact(text: str) -> tuple[str, list[int]]:
        """공백을 지운 문자열과, 각 글자가 원문 어디서 왔는지의 위치표를 만든다.

        공백을 지워야 'SK 하이닉스'가 종목명 'SK하이닉스'와 붙지만, 좌측 경계를
        판정하려면 원문 위치가 필요하다.
        """
        chars: list[str] = []
        origins: list[int] = []
        for position, char in enumerate(text or ""):
            if char.isspace():
                continue
            chars.append(char)
            origins.append(position)
        return "".join(chars), origins

    def _scan_longest_match(self, text: str, index: Mapping[str, str]) -> Counter:
        """좌→우 최장일치 스캔 + 좌측 경계 검사."""
        counts: Counter = Counter()
        source = text or ""
        compacted, origins = self._compact(source)
        length = len(compacted)
        position = 0
        while position < length:
            window = min(_MAX_NAME_LEN, length - position)
            matched = 0
            for size in range(window, _MIN_NAME_LEN - 1, -1):
                candidate = compacted[position : position + size]
                if candidate in index and self._has_left_boundary(source, origins[position]):
                    counts[candidate] += 1
                    matched = size
                    break
            position += matched or 1
        return counts

    @staticmethod
    def _has_left_boundary(source: str, origin: int) -> bool:
        """직전 글자가 낱말 글자면 어절 내부이므로 종목 언급으로 보지 않는다."""
        if origin == 0:
            return True
        return not source[origin - 1].isalnum()

    def count_stock_mentions(self, items: Sequence[Mapping[str, Any]]) -> list[dict]:
        """자막에서 종목 언급 횟수와 언급 영상 수를 집계한다."""
        index = self._build_name_index()
        if not index:
            return []
        totals: Counter = Counter()
        video_spread: Counter = Counter()
        for item in items or []:
            if item.get("skip_reason"):
                continue
            counts = self._scan_longest_match(item.get("transcript", ""), index)
            totals.update(counts)
            video_spread.update(counts.keys())
        mentions = [
            {
                "name": name,
                "code": index[name],
                "count": count,
                "video_count": video_spread[name],
            }
            for name, count in totals.items()
        ]
        # 여러 방송이 공통으로 말한 종목이 한 방송의 반복보다 위에 오게 한다.
        mentions.sort(key=lambda m: (-m["video_count"], -m["count"], m["name"]))
        return mentions[: self._max_mentions]

    # --- AI 요약 --------------------------------------------------------------

    def _chunks(self, text: str) -> list[str]:
        return [
            text[i : i + self._chunk_chars]
            for i in range(0, len(text), self._chunk_chars)
        ] or [""]

    async def summarize_video(self, item: Mapping[str, Any]) -> str:
        """영상 1편을 요약한다. 자막이 길면 청크별로 나눠 부르고 이어붙인다."""
        chunks = self._chunks(str(item.get("transcript") or ""))
        parts: list[str] = []
        for order, chunk in enumerate(chunks, start=1):
            header = (
                f"[채널] {item.get('channel_title', '')}\n"
                f"[제목] {item.get('title', '')}\n"
            )
            if len(chunks) > 1:
                header += f"[구간] {order}/{len(chunks)}\n"
            output = await self._client.complete(
                system=_VIDEO_SYSTEM_PROMPT,
                user=f"{header}[자막]\n{chunk}",
                max_tokens=self._max_tokens,
                temperature=0.2,
                usage_type="youtube",
            )
            parts.append(str(output or "").strip())
        return "\n".join(part for part in parts if part)

    async def build_digest(
        self, items: Sequence[Mapping[str, Any]], *, report_date: str
    ) -> ResCommonResponse:
        usable = [item for item in (items or []) if not item.get("skip_reason")]
        if not usable:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1="자막을 읽을 수 있는 영상이 없습니다.",
                data=None,
            )

        mentions = self.count_stock_mentions(usable)
        videos: list[dict] = []
        failed = 0
        for item in usable:
            try:
                summary = await self.summarize_video(item)
            except Exception as e:
                # 한 편의 업스트림 실패로 하루치 리포트를 통째로 버리지 않는다.
                self._logger.warning(f"영상 요약 실패 ({item.get('video_id')}): {e}")
                summary = ""
                failed += 1
            videos.append(
                {
                    "video_id": item.get("video_id", ""),
                    "title": item.get("title", ""),
                    "channel_title": item.get("channel_title", ""),
                    "url": item.get("url", ""),
                    "published": item.get("published", ""),
                    "summary": summary,
                }
            )

        payload = {
            "report_date": report_date,
            "mentions": [
                {"name": m["name"], "count": m["count"], "video_count": m["video_count"]}
                for m in mentions
            ],
            "videos": [
                {
                    "channel": v["channel_title"],
                    "title": v["title"],
                    "summary": v["summary"],
                }
                for v in videos
                if v["summary"]
            ],
        }
        try:
            digest_text = await self._client.complete(
                system=_DIGEST_SYSTEM_PROMPT,
                user=json.dumps(payload, ensure_ascii=False, default=str, indent=2),
                max_tokens=self._max_tokens,
                temperature=0.2,
                usage_type="youtube",
            )
        except Exception as e:
            self._logger.error(f"유튜브 다이제스트 종합 요약 실패: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=f"AI 종합 요약 실패: {e}",
                data=None,
            )

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상처리 되었습니다.",
            data={
                "report_date": report_date,
                "video_count": len(videos),
                "failed_summary_count": failed,
                "mentions": mentions,
                "videos": videos,
                "digest_text": str(digest_text or "").strip(),
            },
        )
