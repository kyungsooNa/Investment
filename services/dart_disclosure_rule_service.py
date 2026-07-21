"""공시 제목에 기반한 설명 가능한 중요도 규칙."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from services.dart_disclosure_client import DartDisclosure


@dataclass(frozen=True)
class DisclosureImportance:
    score: int
    level: str
    reasons: list[str]


class DartDisclosureRuleService:
    _RULES = (
        (100, ("상장폐지", "거래정지", "감사의견거절", "부적정의견"), "상장·감사 위험 관련 공시"),
        (95, ("횡령", "배임", "회생절차", "영업정지"), "중대한 경영 위험 관련 공시"),
        (85, ("전환사채", "신주인수권부사채", "유상증자", "감자결정"), "자금조달·주식 희석 관련 공시"),
        (80, ("최대주주변경", "합병결정", "회사분할", "주식교환", "주식이전"), "지배구조·기업구조 변경 공시"),
        (70, ("최대주주등소유주식변동",), "최대주주 지분 변동 관련 공시"),
        (80, ("공급계약해지", "계약해지", "계약철회"), "계약 해지·철회 관련 공시"),
        (70, ("단일판매공급계약체결", "공급계약체결"), "공급계약 관련 공시"),
        (70, ("잠정실적", "영업실적", "매출액또는손익구조"), "실적 관련 공시"),
        (60, ("자기주식취득", "자기주식소각", "현금현물배당", "배당결정"), "주주환원 관련 공시"),
        (50, ("신규시설투자", "타법인주식", "대량보유상황", "임원주요주주"), "투자·지분변동 관련 공시"),
        (30, ("사업보고서", "반기보고서", "분기보고서"), "정기보고서"),
        (20, ("기업설명회", "IR개최", "주주총회"), "일정·안내성 공시"),
    )

    def evaluate(self, disclosure: DartDisclosure) -> DisclosureImportance:
        normalized = self._normalize(disclosure.report_name)
        score = 10
        reasons = ["일반 공시"]
        for rule_score, keywords, reason in self._RULES:
            if self._contains_any(normalized, keywords):
                score = rule_score
                reasons = [reason]
                break

        if "정정" in disclosure.report_name:
            reasons.append("기존 공시의 정정 제출")
        if "철" in disclosure.remarks or "철회" in normalized:
            score = max(score, 80)
            reasons.append("철회 표시가 있는 공시")

        return DisclosureImportance(
            score=score,
            level=self._level(score),
            reasons=list(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(
            char for char in str(value or "")
            if char not in " \t\r\nㆍ·-_/[]()"
        ).upper()

    @classmethod
    def _contains_any(cls, text: str, keywords: Iterable[str]) -> bool:
        return any(cls._normalize(keyword) in text for keyword in keywords)

    @staticmethod
    def _level(score: int) -> str:
        if score >= 90:
            return "CRITICAL"
        if score >= 70:
            return "HIGH"
        if score >= 50:
            return "MEDIUM"
        if score >= 30:
            return "NORMAL"
        return "LOW"
