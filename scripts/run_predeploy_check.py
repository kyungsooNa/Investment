"""CLI: 배포 직전 운영 점검 (dry-run pre-deploy check).

Usage:
    python -m scripts.run_predeploy_check                       # live (broker 호출 포함)
    python -m scripts.run_predeploy_check --offline             # 정적 점검만 (CI 친화)
    python -m scripts.run_predeploy_check --paper               # 모의투자 broker 로 점검
    python -m scripts.run_predeploy_check --json                # JSON 출력 (요약 표 대신)

검사 항목:
    - config_validation
    - broker_env_consistency
    - latest_trading_date  (live 시 broker 호출)
    - event_shadow_status
    - websocket_subscription_health   (live 전용)
    - account_snapshot_freshness      (live 전용)

자세한 운영 절차는 docs/operations_runbook.md *배포 체크리스트* 참고.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Windows 기본 콘솔 인코딩(cp949) 에서 em-dash 등 일부 유니코드 출력이 깨지므로 UTF-8 로 재구성한다.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — best-effort
            pass

from services.predeploy_check_service import (
    CheckStatus,
    PreDeployCheckService,
    PreDeployCheckSummary,
)


_STATUS_LABEL = {
    CheckStatus.PASS: "PASS",
    CheckStatus.FAIL: "FAIL",
    CheckStatus.SKIPPED: "SKIP",
    CheckStatus.WARN: "WARN",
}


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("predeploy_check")
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s  %(message)s"))
        logger.addHandler(h)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return logger


async def _bootstrap_live(is_paper_trading: bool, logger: logging.Logger):
    """live 점검에 필요한 최소 서비스 그래프를 만든다.

    실패해도 점검은 계속 진행할 수 있도록 broker / mcs / websocket_probe 를
    Optional 로 반환한다. (None 인 컴포넌트는 해당 check 가 SKIPPED 가 된다.)
    """
    from brokers.broker_api_wrapper import BrokerAPIWrapper
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
    from config.config_loader import load_configs
    from core.market_clock import MarketClock
    from repositories.stock_code_repository import StockCodeRepository
    from services.market_calendar_service import MarketCalendarService

    config_data = load_configs()
    config_dict = config_data.model_dump() if hasattr(config_data, "model_dump") else dict(config_data)

    market_clock = MarketClock(
        market_open_time=config_dict.get("market_open_time", "09:00"),
        market_close_time=config_dict.get("market_close_time", "15:40"),
        timezone=config_dict.get("market_timezone", "Asia/Seoul"),
        logger=logger,
    )
    stock_code_repository = StockCodeRepository(logger=logger)
    mcs = MarketCalendarService(market_clock, logger)

    env = KoreaInvestApiEnv(config_dict, logger)
    env.set_trading_mode(is_paper_trading)
    try:
        token_ok = await env.get_access_token()
    except Exception as exc:  # noqa: BLE001
        logger.warning("토큰 발급 실패 — broker / live check 는 SKIPPED 됩니다: %r", exc)
        return None, None, None

    if not token_ok:
        logger.warning("토큰 발급 실패(falsy) — broker / live check 는 SKIPPED 됩니다.")
        return None, None, None

    broker = BrokerAPIWrapper(
        env=env,
        logger=logger,
        market_clock=market_clock,
        market_calendar_service=mcs,
        stock_code_repository=stock_code_repository,
    )
    mcs.set_broker(broker)

    # WebSocket probe: 운영 환경에서 별도 streaming watchdog 가 도입되기 전까지는
    # placeholder. live 점검은 현재 SKIPPED 로 노출되고, watchdog 도입 시 여기서
    # 실제 last_tick_age_sec 등을 채워 반환하면 된다.
    websocket_probe = None

    return broker, mcs, websocket_probe


def _format_table(summary: PreDeployCheckSummary) -> str:
    rows = []
    rows.append(f"{'STATUS':<6}  {'CHECK':<32}  {'ELAPSED':<8}  DETAIL")
    rows.append("-" * 100)
    for r in summary.results:
        rows.append(
            f"{_STATUS_LABEL[r.status]:<6}  {r.name:<32}  {r.elapsed_ms:>5} ms  {r.detail}"
        )
    rows.append("-" * 100)
    rows.append("  ".join(f"{k}={v}" for k, v in summary.counts.items()))
    return "\n".join(rows)


def _format_json(summary: PreDeployCheckSummary) -> str:
    payload = {
        "results": [
            {
                "name": r.name,
                "status": r.status.value,
                "detail": r.detail,
                "elapsed_ms": r.elapsed_ms,
            }
            for r in summary.results
        ],
        "counts": summary.counts,
        "has_failure": summary.has_failure,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="배포 직전 운영 점검 (operations_runbook.md 배포 체크리스트 자동화)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="broker / WebSocket 호출 없이 정적 점검만 수행 (CI 용)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="모의투자 모드로 broker 초기화 (live 점검 시에만 의미 있음)",
    )
    parser.add_argument(
        "--event-shadow-dir",
        default="logs/strategies/event_shadow",
        help="event shadow 로그 디렉터리 (default: logs/strategies/event_shadow)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="JSON 으로 출력 (요약 표 대신)",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from config.config_loader import load_configs

    logger = _make_logger()

    broker = None
    mcs = None
    websocket_probe = None
    if not args.offline:
        broker, mcs, websocket_probe = await _bootstrap_live(args.paper, logger)

    service = PreDeployCheckService(
        config_loader=load_configs,
        market_calendar_service=mcs,
        broker=broker,
        websocket_probe=websocket_probe,
        event_shadow_dir=args.event_shadow_dir,
    )

    summary = await service.run_all(offline=args.offline)

    if args.as_json:
        print(_format_json(summary))
    else:
        print(_format_table(summary))

    return 1 if summary.has_failure else 0


def main() -> None:
    args = _parse_args()
    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
