"""유튜브 채널 자막 수집 → 종목 언급 집계 → AI 다이제스트 dry-run 진단.

config.yaml 의 ai_analysis 설정으로 등록 채널의 최근 영상 자막을 실제로 받아
언급 집계와 AI 요약까지 한 번에 확인한다. 웹 서버·브로커 인증 없이 검증 가능.

**--ai 를 주면 실제 AI 요청을 보내 일일 한도를 소비한다.** 소비량은
(영상 수 + 긴 영상의 추가 청크 수 + 종합 1건) 이다. 기본은 수집·집계만 한다.

사용:
    python scripts/check_youtube_ai.py                       # 수집+언급집계만 (한도 0건)
    python scripts/check_youtube_ai.py --ai --max-videos 2   # AI 요약까지 (한도 소비)
    python scripts/check_youtube_ai.py --hours 48            # 최근 48시간 영상
    python scripts/check_youtube_ai.py --debug               # 실패 시 traceback
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

from repositories.stock_code_repository import StockCodeRepository  # noqa: E402
from repositories.youtube_channel_repository import (  # noqa: E402
    YoutubeChannelRepository,
)
from services.ai_client import AiClient  # noqa: E402
from services.ai_usage_limiter import AiUsageLimiter  # noqa: E402
from services.youtube_digest_service import YoutubeDigestService  # noqa: E402
from services.youtube_transcript_collector_service import (  # noqa: E402
    YoutubeTranscriptCollectorService,
)

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
    """(AiClient, YoutubeDigestService) 또는 (None, None) 반환.

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
        timeout_sec=float(ai_cfg.get("timeout_sec", 45)),
        reasoning_effort=str(ai_cfg.get("reasoning_effort") or ""),
        usage_limiter=limiter,
        max_concurrent_requests=int(ai_cfg.get("max_concurrent_requests", 0)),
        min_request_interval_sec=float(ai_cfg.get("min_request_interval_sec", 0.0)),
        max_input_chars=int(ai_cfg.get("max_input_chars", 0)),
    )
    # 입력 예산보다 청크를 작게 잡아야 AiClient 가 뒤를 잘라내지 않는다.
    budget = int(ai_cfg.get("max_input_chars", 0)) or 24000
    digest = YoutubeDigestService(
        ai_client,
        stock_code_repository=StockCodeRepository(),
        max_tokens=int(ai_cfg.get("max_tokens", 2048)),
        chunk_chars=max(2000, budget - 2000),
    )
    return ai_client, digest


def _print_mentions(mentions: list) -> None:
    if not mentions:
        print("  (언급된 종목 없음)")
        return
    for rank, mention in enumerate(mentions[:20], start=1):
        print(
            f"  {rank:2d}. {mention['name']:<14s} "
            f"언급 {mention['count']:3d}회 / {mention['video_count']}개 영상"
        )


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24.0, help="최근 N시간 영상 (기본 24)")
    parser.add_argument("--max-videos", type=int, default=3, help="채널당 영상 수 상한")
    parser.add_argument("--ai", action="store_true", help="AI 요약까지 실행(한도 소비)")
    parser.add_argument("--debug", action="store_true", help="실패 시 전체 traceback 출력")
    args = parser.parse_args()

    config = _load_config()
    ai_cfg = config.get("ai_analysis") or {}

    repo = YoutubeChannelRepository()
    await repo.seed_defaults()
    channels = await repo.get_all(enabled_only=True)
    if not channels:
        print("[안내] 등록된 채널이 없습니다.")
        return

    ai_client, digest = _build_ai(ai_cfg) if args.ai else (None, None)
    if args.ai and digest is None:
        print("[안내] config.yaml 의 ai_analysis.enabled 가 false 라 AI 요약을 건너뜁니다.")

    print(f"채널={len(channels)}개  최근={args.hours}시간  채널당 상한={args.max_videos}편")
    print(f"AI 요약={'ON' if digest else 'OFF'}\n")

    collector = YoutubeTranscriptCollectorService()
    try:
        items = await collector.collect(
            channels, since_hours=args.hours, max_videos_per_channel=args.max_videos
        )
    except Exception as e:
        if args.debug:
            raise
        print(f"[오류] 수집 실패: {type(e).__name__}: {e}")
        return

    usable = [item for item in items if not item.get("skip_reason")]
    print(f"[수집] 영상 {len(items)}편 중 자막 확보 {len(usable)}편")
    if collector.was_blocked:
        print(
            "\n[중단] YouTube 가 이 IP를 차단했습니다(IpBlocked). 짧은 시간에 반복 실행하면"
            " 걸립니다 — 몇 분~몇 시간 뒤 다시 시도하세요.\n"
        )
    for item in items:
        mark = "OK  " if not item.get("skip_reason") else f"SKIP({item['skip_reason']})"
        print(
            f"  {mark} [{item.get('channel_title', '')}] "
            f"{item.get('title', '')[:50]} ({len(item.get('transcript', ''))}자)"
        )

    counter = digest or YoutubeDigestService(
        None, stock_code_repository=StockCodeRepository()
    )
    print("\n[종목 언급 집계] (AI 미사용, 최장일치 스캔)")
    _print_mentions(counter.count_stock_mentions(usable))

    if digest is None:
        print("\n[안내] AI 요약을 보려면 --ai 를 붙이세요.")
        return

    print("\n[AI 다이제스트] 생성 중...")
    try:
        result = await digest.build_digest(usable, report_date="dryrun")
    except Exception as e:
        if args.debug:
            raise
        print(f"[오류] AI 실패: {type(e).__name__}: {e}")
        return

    if result.rt_cd != "0":
        print(f"[실패] {result.msg1}")
        return
    print(result.data["digest_text"])
    if result.data["failed_summary_count"]:
        print(f"\n[주의] 요약 실패 {result.data['failed_summary_count']}편")


if __name__ == "__main__":
    asyncio.run(_main())
