"""공시 → 규칙 점수 → AI 요약 1차 파이프라인 dry-run 진단.

config.yaml 의 dart_disclosure/ai_analysis 설정으로 실제 OpenDART 공시를 받아
규칙 점수와 AI 요약까지 한 번에 확인한다. 텔레그램·실시간 대기 없이 검증 가능.

사용:
    python scripts/check_disclosure_ai.py                  # 오늘, 관심종목
    python scripts/check_disclosure_ai.py --date 20260715  # 특정일
    python scripts/check_disclosure_ai.py --codes 000660   # 특정 종목
    python scripts/check_disclosure_ai.py --all --limit 10 # 관심종목 무시, 전체 일부
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repositories.favorite_repository import FavoriteRepository  # noqa: E402
from services.ai_client import AiClient  # noqa: E402
from services.ai_disclosure_analyzer import AiDisclosureAnalyzer  # noqa: E402
from services.dart_disclosure_client import DartDisclosureClient  # noqa: E402
from services.dart_disclosure_rule_service import DartDisclosureRuleService  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_DEFAULT_MODEL = "gemini-2.5-flash"


def _load_config() -> dict:
    config_path = _ROOT / "config" / "config.yaml"
    if not config_path.exists():
        print(f"[오류] {config_path} 가 없습니다.")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def _fetch(client, date, max_pages):
    collected = []
    for page_no in range(1, max_pages + 1):
        page = await client.fetch_disclosures(date, page_no=page_no)
        collected.extend(page.items)
        if page_no >= page.total_page:
            break
    return collected


def _build_analyzer(ai_cfg: dict):
    ai_key = str(ai_cfg.get("api_key") or "")
    if not (ai_cfg.get("enabled") and ai_key):
        return None
    ai_client = AiClient(
        base_url=str(ai_cfg.get("base_url") or _DEFAULT_BASE_URL),
        api_key=ai_key,
        model=str(ai_cfg.get("model") or _DEFAULT_MODEL),
        timeout_sec=float(ai_cfg.get("timeout_sec", 15)),
    )
    return AiDisclosureAnalyzer(ai_client, max_tokens=int(ai_cfg.get("max_tokens", 256)))


async def _resolve_targets(args):
    if args.codes:
        return {c.strip() for c in args.codes.split(",") if c.strip()}
    if args.all:
        return None  # 필터 없음
    return {str(c) for c in await FavoriteRepository().get_all()}


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="YYYYMMDD (기본: 오늘 KST)")
    parser.add_argument("--codes", default=None, help="쉼표구분 종목코드 (관심종목 대신)")
    parser.add_argument("--all", action="store_true", help="관심종목 필터 없이 전체")
    parser.add_argument("--limit", type=int, default=20, help="표시 건수 상한")
    args = parser.parse_args()

    config = _load_config()
    dart_cfg = config.get("dart_disclosure") or {}
    ai_cfg = config.get("ai_analysis") or {}

    dart_key = str(dart_cfg.get("api_key") or "")
    if not dart_key:
        print("[오류] dart_disclosure.api_key 가 비어 있습니다.")
        sys.exit(1)

    date = args.date or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    threshold = int(dart_cfg.get("immediate_alert_score", 70))
    max_pages = int(dart_cfg.get("max_pages_per_poll", 5))

    client = DartDisclosureClient(
        dart_key, timeout_sec=float(dart_cfg.get("request_timeout_sec", 5))
    )
    rules = DartDisclosureRuleService()
    analyzer = _build_analyzer(ai_cfg)

    targets = await _resolve_targets(args)
    if targets is not None and not targets:
        print("[안내] 관심종목이 비어 있습니다. --codes 또는 --all 로 실행하세요.")
        return

    label = "전체" if targets is None else (", ".join(sorted(targets)) or "없음")
    print(f"조회일={date}  대상={label}  즉시알림 임계={threshold}점")
    print(f"AI 요약={'ON' if analyzer else 'OFF'}")
    print("OpenDART 조회 중...")

    try:
        disclosures = await _fetch(client, date, max_pages)
    except Exception as exc:
        print(f"[실패] 공시 조회 오류: {type(exc).__name__}: {exc}")
        print("→ dart_disclosure.api_key·네트워크를 확인하세요.")
        sys.exit(1)

    if targets is not None:
        disclosures = [d for d in disclosures if d.stock_code in targets]

    if not disclosures:
        print("해당 조건의 공시가 없습니다.")
        return

    print(f"\n총 {len(disclosures)}건 (상위 {min(args.limit, len(disclosures))}건 표시)\n")
    for disclosure in disclosures[: args.limit]:
        importance = rules.evaluate(disclosure)
        print(
            f"[{importance.level} {importance.score}점] "
            f"{disclosure.corp_name}({disclosure.stock_code}) — {disclosure.report_name}"
        )
        print(f"   근거: {', '.join(importance.reasons)}")
        if analyzer and importance.score >= threshold:
            summary = await analyzer.summarize(disclosure, importance)
            print(f"   🤖 AI: {summary or '(요약 실패 — 규칙 판정으로 폴백)'}")
        print()


if __name__ == "__main__":
    asyncio.run(_main())
