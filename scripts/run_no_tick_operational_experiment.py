"""Run a selected no-tick operational experiment cohort.

Default mode is a dry run: it validates the plan and writes the selected cohort
without opening a WebSocket. Pass --execute-live during market hours to connect
to KIS, subscribe unified price streams, wait, and persist tick-ingest deltas.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

Sleeper = Callable[[float], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_plan(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def select_experiment(plan: Dict[str, Any], experiment_id: str) -> Dict[str, Any]:
    for item in plan.get("experiments", []):
        if item.get("id") == experiment_id:
            return item
    available = ", ".join(str(item.get("id")) for item in plan.get("experiments", []))
    raise ValueError(f"Experiment id not found: {experiment_id}. Available: {available}")


def _empty_stats() -> Dict[str, int]:
    return {"received": 0, "quality_reject": 0, "dispatched": 0, "malformed": 0}


def _snapshot(price_stream_service: Any, codes: List[str]) -> Dict[str, Dict[str, int]]:
    snapper = getattr(price_stream_service, "tick_ingest_stats_snapshot")
    raw = snapper(codes)
    return {
        code: {
            "received": _to_int((raw.get(code) or {}).get("received")),
            "quality_reject": _to_int((raw.get(code) or {}).get("quality_reject")),
            "dispatched": _to_int((raw.get(code) or {}).get("dispatched")),
            "malformed": _to_int((raw.get(code) or {}).get("malformed")),
        }
        for code in codes
    }


def _build_per_code(
    experiment: Dict[str, Any],
    before: Dict[str, Dict[str, int]],
    after: Dict[str, Dict[str, int]],
    subscriptions: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    rows_by_code = {str(row.get("code")): row for row in experiment.get("rows", []) if row.get("code")}
    out: Dict[str, Dict[str, Any]] = {}
    for code in [str(c) for c in experiment.get("codes", [])]:
        start = before.get(code) or _empty_stats()
        end = after.get(code) or _empty_stats()
        row = rows_by_code.get(code, {})
        item = {
            "code": code,
            "name": row.get("name", ""),
            "instrument_type": row.get("instrument_type", ""),
            "subscribe_ok": subscriptions.get(code, {}).get("subscribe_ok"),
            "ack_ok": subscriptions.get(code, {}).get("ack_ok"),
            "error": subscriptions.get(code, {}).get("error"),
        }
        for key in ("received", "quality_reject", "dispatched", "malformed"):
            item[f"{key}_before"] = _to_int(start.get(key))
            item[f"{key}_after"] = _to_int(end.get(key))
            item[f"{key}_delta"] = item[f"{key}_after"] - item[f"{key}_before"]
        out[code] = item
    return out


def _summarize(per_code: Dict[str, Dict[str, Any]], *, classify_ticks: bool = True) -> Dict[str, Any]:
    if not classify_ticks:
        return {
            "total_codes": len(per_code),
            "received_codes": 0,
            "no_tick_codes": 0,
            "received_code_list": [],
            "no_tick_code_list": [],
            "subscribe_failures": [],
            "ack_failures": [],
        }
    received = [code for code, row in per_code.items() if _to_int(row.get("received_delta")) > 0]
    no_tick = [code for code, row in per_code.items() if _to_int(row.get("received_delta")) <= 0]
    subscribe_failures = [code for code, row in per_code.items() if row.get("subscribe_ok") is False]
    ack_failures = [code for code, row in per_code.items() if row.get("ack_ok") is False]
    return {
        "total_codes": len(per_code),
        "received_codes": len(received),
        "no_tick_codes": len(no_tick),
        "received_code_list": received,
        "no_tick_code_list": no_tick,
        "subscribe_failures": subscribe_failures,
        "ack_failures": ack_failures,
    }


def build_dry_run_result(*, experiment: Dict[str, Any], duration_sec: int) -> Dict[str, Any]:
    codes = [str(code) for code in experiment.get("codes", [])]
    subscriptions = {
        code: {"subscribe_ok": None, "ack_ok": None, "error": None}
        for code in codes
    }
    before = {code: _empty_stats() for code in codes}
    after = {code: _empty_stats() for code in codes}
    per_code = _build_per_code(experiment, before, after, subscriptions)
    return {
        "status": "dry_run",
        "experiment_id": experiment.get("id", ""),
        "goal": experiment.get("goal", ""),
        "expected_signal": experiment.get("expected_signal", ""),
        "duration_sec": duration_sec,
        "started_at": None,
        "ended_at": None,
        "summary": _summarize(per_code, classify_ticks=False),
        "per_code": per_code,
    }


async def run_experiment(
    *,
    experiment: Dict[str, Any],
    streaming_service: Any,
    price_stream_service: Any,
    duration_sec: int,
    ack_timeout_sec: float = 2.0,
    sleeper: Sleeper = asyncio.sleep,
) -> Dict[str, Any]:
    codes = [str(code) for code in experiment.get("codes", [])]
    subscriptions: Dict[str, Dict[str, Any]] = {
        code: {"subscribe_ok": None, "ack_ok": None, "error": None}
        for code in codes
    }
    before: Dict[str, Dict[str, int]] = {code: _empty_stats() for code in codes}
    after: Dict[str, Dict[str, int]] = {code: _empty_stats() for code in codes}
    started_at = _now_iso()
    connected = False

    try:
        callback = getattr(streaming_service, "dispatch_realtime_message", None)
        connected = bool(await streaming_service.connect_websocket(callback=callback))
        if not connected:
            raise RuntimeError("WebSocket connect failed")

        before = _snapshot(price_stream_service, codes)
        waiter = getattr(streaming_service, "wait_unified_price_ack", None)

        for code in codes:
            try:
                subscribe_ok = bool(await streaming_service.subscribe_unified_price(code))
                ack_ok = True
                if subscribe_ok and callable(waiter):
                    ack_ok = bool(await waiter(code, ack_timeout_sec))
                subscriptions[code].update({"subscribe_ok": subscribe_ok, "ack_ok": ack_ok})
            except Exception as exc:
                subscriptions[code].update({"subscribe_ok": False, "ack_ok": False, "error": str(exc)})

        await sleeper(float(duration_sec))
        after = _snapshot(price_stream_service, codes)
    finally:
        for code in codes:
            if subscriptions.get(code, {}).get("subscribe_ok"):
                try:
                    await streaming_service.unsubscribe_unified_price(code)
                except Exception as exc:
                    subscriptions[code]["unsubscribe_error"] = str(exc)
        if connected:
            await streaming_service.disconnect_websocket()

    per_code = _build_per_code(experiment, before, after, subscriptions)
    return {
        "status": "completed",
        "experiment_id": experiment.get("id", ""),
        "goal": experiment.get("goal", ""),
        "expected_signal": experiment.get("expected_signal", ""),
        "duration_sec": duration_sec,
        "started_at": started_at,
        "ended_at": _now_iso(),
        "summary": _summarize(per_code),
        "per_code": per_code,
    }


def format_markdown_result(result: Dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        f"# No-Tick Experiment Result: {result.get('experiment_id', '')}",
        "",
        "## Summary",
        "",
        f"- Status: `{result.get('status', '')}`",
        f"- Duration: `{result.get('duration_sec', 0)}s`",
        f"- Total codes: {summary.get('total_codes', 0)}",
        f"- Received codes: {summary.get('received_codes', 0)}",
        f"- No-tick codes: {summary.get('no_tick_codes', 0)}",
        f"- Subscribe failures: {', '.join(summary.get('subscribe_failures') or []) or 'none'}",
        f"- ACK failures: {', '.join(summary.get('ack_failures') or []) or 'none'}",
        "",
        "## Cohort",
        "",
        f"- Goal: {result.get('goal', '')}",
        f"- Expected signal: {result.get('expected_signal', '')}",
        "",
        "| Code | Name | Type | Subscribed | ACK | Received Delta | Dispatch Delta | Reject Delta |",
        "|------|------|------|------------|-----|---------------:|---------------:|-------------:|",
    ]
    for code, row in result.get("per_code", {}).items():
        lines.append(
            f"| {code} | {row.get('name', '')} | {row.get('instrument_type', '')} | "
            f"{row.get('subscribe_ok')} | {row.get('ack_ok')} | "
            f"{row.get('received_delta', 0)} | {row.get('dispatched_delta', 0)} | "
            f"{row.get('quality_reject_delta', 0)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_outputs(result: Dict[str, Any], output_json: Optional[Path], output_markdown: Optional[Path]) -> None:
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] JSON result: {output_json}")
    if output_markdown:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(format_markdown_result(result), encoding="utf-8")
        print(f"[INFO] Markdown result: {output_markdown}")
    if not (output_json or output_markdown):
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _default_output_paths(experiment_id: str) -> Tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in experiment_id)
    base = Path("reports") / f"no_tick_operational_experiment_result_{safe_id}_{stamp}"
    return base.with_suffix(".json"), base.with_suffix(".md")


def _make_logger(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("no_tick_experiment_runner")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s  %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger


async def bootstrap_live_services(*, is_paper_trading: bool, logger: logging.Logger):
    from brokers.broker_api_wrapper import BrokerAPIWrapper
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
    from config.config_loader import load_configs
    from core.market_clock import MarketClock
    from repositories.stock_code_repository import StockCodeRepository
    from repositories.stock_repository import StockRepository
    from services.market_calendar_service import MarketCalendarService
    from services.price_stream_service import PriceStreamService
    from services.streaming_service import StreamingService

    config_data = load_configs()
    if hasattr(config_data, "model_dump"):
        config_dict = config_data.model_dump()
    elif hasattr(config_data, "dict"):
        config_dict = config_data.dict()
    else:
        config_dict = config_data

    market_clock = MarketClock(
        market_open_time=config_dict.get("market_open_time", "09:00"),
        market_close_time=config_dict.get("market_close_time", "15:40"),
        timezone=config_dict.get("market_timezone", "Asia/Seoul"),
        logger=logger,
    )
    stock_code_repository = StockCodeRepository(logger=logger)
    market_calendar_service = MarketCalendarService(market_clock, logger)

    env = KoreaInvestApiEnv(config_dict, logger)
    env.set_trading_mode(is_paper_trading)
    if not await env.get_access_token():
        raise RuntimeError("KIS access token issue failed")

    broker = BrokerAPIWrapper(
        env=env,
        logger=logger,
        market_clock=market_clock,
        market_calendar_service=market_calendar_service,
        stock_code_repository=stock_code_repository,
    )
    market_calendar_service.set_broker(broker)

    stock_repository = StockRepository(logger=logger)
    price_stream_service = PriceStreamService(stock_repo=stock_repository, logger=logger)
    streaming_service = StreamingService(
        broker_api_wrapper=broker,
        logger=logger,
        market_clock=market_clock,
        price_stream_service=price_stream_service,
    )
    return streaming_service, price_stream_service, broker


async def _execute_live(args: argparse.Namespace, experiment: Dict[str, Any]) -> Dict[str, Any]:
    logger = _make_logger(args.verbose)
    streaming_service, price_stream_service, broker = await bootstrap_live_services(
        is_paper_trading=args.paper,
        logger=logger,
    )
    try:
        return await run_experiment(
            experiment=experiment,
            streaming_service=streaming_service,
            price_stream_service=price_stream_service,
            duration_sec=args.duration_sec,
            ack_timeout_sec=args.ack_timeout_sec,
        )
    finally:
        stopper = getattr(broker, "stop", None)
        if callable(stopper):
            await stopper()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="Experiment plan JSON path.")
    parser.add_argument("--experiment-id", required=True, help="Experiment id to run.")
    parser.add_argument("--duration-sec", type=int, default=180, help="Live subscription duration in seconds.")
    parser.add_argument("--ack-timeout-sec", type=float, default=2.0, help="Unified price ACK wait timeout.")
    parser.add_argument("--output-json", type=Path, help="Result JSON path.")
    parser.add_argument("--output-markdown", type=Path, help="Result Markdown path.")
    parser.add_argument("--execute-live", action="store_true", help="Open KIS WebSocket and run the cohort.")
    parser.add_argument("--paper", action="store_true", help="Use paper-trading KIS config for live execution.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    experiment = select_experiment(plan, args.experiment_id)

    output_json = args.output_json
    output_markdown = args.output_markdown
    if args.execute_live and not (output_json or output_markdown):
        output_json, output_markdown = _default_output_paths(args.experiment_id)

    if args.execute_live:
        result = asyncio.run(_execute_live(args, experiment))
    else:
        result = build_dry_run_result(experiment=experiment, duration_sec=args.duration_sec)

    _write_outputs(result, output_json, output_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
