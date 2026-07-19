"""종목 뉴스 수집 → AI 검토 파이프라인 dry-run 진단.

config.yaml 의 ai_analysis 설정으로 네이버 종목뉴스를 실제로 스크래핑하고
AI 검토까지 한 번에 확인한다. 웹 서버·브로커 인증 없이 검증 가능.

**이 스크립트는 실제 AI 요청을 보내 일일 한도를 소비한다.**
한도를 쓰지 않고 스크래퍼만 확인하려면 --no-ai 를 쓴다.
자동화된 테스트는 전부 모킹되어 있으며 이 스크립트를 호출하지 않는다.

사용:
    python scripts/check_news_ai.py --codes 005930           # 1종목 수집+AI (한도 1건)
    python scripts/check_news_ai.py --codes 005930 --no-ai   # 수집만 (한도 0건)
    python scripts/check_news_ai.py                          # 관심종목 전체 수집만 미리보기
    python scripts/check_news_ai.py --codes 005930 --debug   # 실패 시 traceback
"""
import argparse
import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 콘솔(cp949)이 처리 못 하는 문자에서 출력이 잘리지 않도록 UTF-8 고정.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from repositories.favorite_repository import FavoriteRepository  # noqa: E402
from services.ai_client import AiClient  # noqa: E402
from services.ai_news_analyzer import AiNewsAnalyzer  # noqa: E402
from services.ai_usage_limiter import AiUsageLimiter  # noqa: E402
from services.stock_news_collector_service import StockNewsCollectorService  # noqa: E402

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


def _build_ai(ai_cfg: dict):
    """(AiClient, AiNewsAnalyzer) 또는 (None, None) 반환.

    앱과 같은 usage_limiter 를 붙여 이 스크립트의 소비도 일일 한도에 집계된다.
    """
    if not ai_cfg.get("enabled"):
        return None, None
    limiter = AiUsageLimiter(
        daily_request_limit=int(ai_cfg.get("daily_request_limit", 100)),
        disclosure_reserve=int(ai_cfg.get("disclosure_reserve", 20)),
    )
    ai_client = AiClient(
        base_url=str(ai_cfg.get("base_url") or _DEFAULT_BASE_URL),
        api_key=str(ai_cfg.get("api_key") or ""),
        model=str(ai_cfg.get("model") or _DEFAULT_MODEL),
        timeout_sec=float(ai_cfg.get("timeout_sec", 15)),
        usage_limiter=limiter,
    )
    analyzer = AiNewsAnalyzer(ai_client, max_tokens=int(ai_cfg.get("max_tokens", 2048)))
    return ai_client, analyzer


async def _resolve_targets(args):
    if args.codes:
        return [c.strip() for c in args.codes.split(",") if c.strip()]
    return [str(c) for c in await FavoriteRepository().get_all()]


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default=None, help="쉼표구분 종목코드 (기본: 관심종목)")
    parser.add_argument("--limit", type=int, default=15, help="종목당 기사 수 상한")
    parser.add_argument("--no-ai", action="store_true", help="수집만 하고 AI 호출 생략(한도 0건)")
    parser.add_argument("--debug", action="store_true", help="AI 실패 시 전체 traceback 출력")
    args = parser.parse_args()

    config = _load_config()
    ai_cfg = config.get("ai_analysis") or {}

    targets = await _resolve_targets(args)
    if not targets:
        print("[안내] 관심종목이 비어 있습니다. --codes 로 종목을 지정하세요.")
        return

    # --codes 없이 관심종목 전체를 돌 때 한도를 대량 소비하지 않도록 막는다.
    use_ai = not args.no_ai and bool(args.codes)
    ai_client, analyzer = _build_ai(ai_cfg) if use_ai else (None, None)
    if not args.no_ai and not args.codes:
        print("[안내] 관심종목 전체 실행은 수집만 합니다. AI 검토는 --codes 로 지정하세요.")

    collector = StockNewsCollectorService()
    print(f"대상={', '.join(targets)}  기사상한={args.limit}")
    print(f"AI 검토={'ON' if analyzer else 'OFF'}\n")

    for code in targets:
        news = await collector.collect(code, limit=args.limit)
        print(f"[{code}] 뉴스 {len(news)}건")
        for article in news:
            print(f"   - {article['published_at']} | {article['press']} | {article['title']}")
        if not news:
            print("   (수집 결과 없음 — Referer/clusterId 또는 HTML 구조 변경 확인)")
        if analyzer and news:
            await _review(analyzer, code, news, debug=args.debug)
        print()

    if ai_client is not None:
        await _print_usage(ai_cfg)


async def _review(analyzer, code: str, news: list, *, debug: bool) -> None:
    try:
        result = await analyzer.analyze({"code": code, "name": code, "news": news})
    except Exception as exc:
        print(f"   🤖 AI 검토 실패: {type(exc).__name__}: {exc}")
        if debug:
            import traceback

            traceback.print_exc()
        return
    print("   🤖 AI 검토:")
    for line in (result or "(빈 응답)").splitlines():
        print(f"      {line}")


async def _print_usage(ai_cfg: dict) -> None:
    limiter = AiUsageLimiter(
        daily_request_limit=int(ai_cfg.get("daily_request_limit", 100)),
        disclosure_reserve=int(ai_cfg.get("disclosure_reserve", 20)),
    )
    snapshot = await limiter.get_snapshot()
    print(
        f"[사용량] 오늘 {snapshot['used']}/{snapshot['daily_limit']}건 "
        f"(남음 {snapshot['remaining']}) by_type={snapshot['by_type']}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
