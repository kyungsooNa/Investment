"""CLI: active live-strategy period backtest runner.

Usage:
    python -m scripts.run_backtest --strategy oneil_pocket_pivot --dates 20260501,20260502
    python -m scripts.run_backtest --start-date 20260501 --end-date 20260510 --initial-cash 10000000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ACTIVE_BACKTEST_STRATEGIES = (
    "oneil_pocket_pivot",
    "oneil_squeeze_breakout",
    "high_tight_flag",
    "first_pullback",
    "larry_williams_vbo",
    "rsi2_pullback",
    "larry_williams_channel_breakout",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="기간 백테스트 — 활성 LiveStrategy contract를 replay 데이터로 실행",
    )
    parser.add_argument(
        "--strategy",
        default="oneil_pocket_pivot",
        choices=list(ACTIVE_BACKTEST_STRATEGIES),
        help="실행할 전략 (default: oneil_pocket_pivot)",
    )
    parser.add_argument("--dates", default=None, help="실행 날짜 목록 (YYYYMMDD, 쉼표 구분)")
    parser.add_argument("--start-date", default=None, dest="start_date", help="시작일 YYYYMMDD")
    parser.add_argument("--end-date", default=None, dest="end_date", help="종료일 YYYYMMDD")
    parser.add_argument("--initial-cash", type=float, default=10_000_000, dest="initial_cash")
    parser.add_argument("--max-positions", type=int, default=None, dest="max_positions")
    parser.add_argument(
        "--backtest-time",
        default="12:00:00",
        dest="backtest_time",
        help="전략과 유니버스가 참조할 과거 장중 시각 HH:MM:SS (default: 12:00:00)",
    )
    parser.add_argument(
        "--execution-bar-policy",
        default="current_bar",
        choices=["current_bar", "next_bar"],
        dest="execution_bar_policy",
        help="체결 후보 봉 선택 정책: current_bar=가격에 닿은 첫 분봉, next_bar=가격에 닿은 신호 봉 다음 분봉",
    )
    parser.add_argument("--output", default="console", choices=["console", "json"])
    parser.add_argument("--output-file", default=None, dest="output_file")
    parser.add_argument(
        "--use-risk-sizing",
        action="store_true",
        default=False,
        help="운영 설정 기반 PositionSizing/RiskGate dry-run을 기간 백테스트에 적용",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        default=False,
        help="기간을 train/tune/test 창으로 나누어 walk-forward 검증을 실행",
    )
    parser.add_argument("--wf-train-days", type=int, default=20, dest="wf_train_days")
    parser.add_argument("--wf-tune-days", type=int, default=5, dest="wf_tune_days")
    parser.add_argument("--wf-test-days", type=int, default=5, dest="wf_test_days")
    parser.add_argument("--wf-step-days", type=int, default=None, dest="wf_step_days")
    parser.add_argument(
        "--wf-embargo-days",
        type=int,
        default=0,
        dest="wf_embargo_days",
        help="walk-forward tune/test 경계 사이에 제외할 거래일 수 (default: 0)",
    )
    parser.add_argument(
        "--monte-carlo",
        action="store_true",
        default=False,
        help="완료 trade net_pnl 순서를 섞어 MDD/연속손실/ruin probability를 계산",
    )
    parser.add_argument("--mc-runs", type=int, default=1000, dest="mc_runs")
    parser.add_argument("--mc-seed", type=int, default=None, dest="mc_seed")
    parser.add_argument(
        "--mc-ruin-drawdown-pct",
        type=float,
        default=30.0,
        dest="mc_ruin_drawdown_pct",
        help="MDD가 이 비율 이상이면 ruin으로 집계 (default: 30)",
    )
    parser.add_argument(
        "--profitability-gate",
        action="store_true",
        default=False,
        dest="profitability_gate",
        help="전략별 실전 투입 수익성 기준선 통과 여부를 산출",
    )
    parser.add_argument(
        "--ablation",
        default=None,
        help=(
            "Ablation 백테스트 대상 전략 키 (예: oneil_pocket_pivot). 지정하면 baseline "
            "실행 후 preset 의 각 variant 를 동일 기간/데이터로 재실행해 metric 차이를 출력."
        ),
    )
    parser.add_argument(
        "--ablation-variants",
        default=None,
        dest="ablation_variants",
        help="실행할 variant 이름 (쉼표 구분). 미지정 시 preset 의 모든 variant 실행.",
    )
    parser.add_argument(
        "--parameter-stability",
        default=None,
        dest="parameter_stability",
        help=(
            "Parameter stability sweep 대상 전략 키 (예: oneil_pocket_pivot). 지정하면 "
            "baseline 실행 후 preset 의 각 dimension·sweep 점을 재실행해 metric surface 출력."
        ),
    )
    parser.add_argument(
        "--parameter-stability-dimensions",
        default=None,
        dest="parameter_stability_dimensions",
        help="실행할 dimension 이름 (쉼표 구분). 미지정 시 preset 의 모든 dimension 실행.",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=False,
        help="모의투자 모드로 서비스 그래프 초기화. 과거 분봉/프로그램매매 API는 실전 전용이라 기본은 실전 데이터 모드",
    )
    return parser.parse_args()


def _build_dates(args: argparse.Namespace) -> list[str]:
    if args.dates:
        return [date.strip() for date in str(args.dates).split(",") if date.strip()]
    if not args.start_date or not args.end_date:
        raise ValueError("--dates 또는 --start-date/--end-date를 지정해야 합니다.")

    start = datetime.strptime(args.start_date, "%Y%m%d").date()
    end = datetime.strptime(args.end_date, "%Y%m%d").date()
    if end < start:
        raise ValueError("--end-date는 --start-date보다 빠를 수 없습니다.")

    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _get_program_provider(stock_query_service: Any) -> Any | None:
    market_data_service = getattr(stock_query_service, "market_data_service", None)
    return getattr(market_data_service, "_broker_api_wrapper", None)


def _build_replay_bar_providers(replay_sqs: Any) -> tuple[Any, Any]:
    from services.backtest_replay_adapter import (
        StockQueryDailyMtmBarProvider,
        StockQueryIntradayReplayBarProvider,
    )

    return (
        StockQueryIntradayReplayBarProvider(replay_sqs),
        StockQueryDailyMtmBarProvider(replay_sqs),
    )


class _BacktestLedgerAccountSnapshotCache:
    """AccountSnapshotCache contract backed by the in-memory backtest ledger."""

    def __init__(self, ledger) -> None:
        self._ledger = ledger

    async def get(self, exchange=None):
        from core.account_snapshot import AccountSnapshot

        positions = {
            code: int(position.market_value_basis)
            for code, position in self._ledger.positions.items()
        }
        total_equity = int(self._ledger.cash + sum(positions.values()))
        return AccountSnapshot(
            total_equity=total_equity,
            available_cash=int(self._ledger.available_cash),
            positions=positions,
        )


class _BacktestStrategyRiskProvider:
    """StrategyRiskDataProvider contract backed by the in-memory backtest ledger."""

    def __init__(self, ledger) -> None:
        self._ledger = ledger

    def is_holding(self, strategy_name: str, code: str) -> bool:
        position = self._ledger.positions.get(code)
        return bool(position and position.strategy == strategy_name and position.qty > 0)

    def get_holds_by_strategy(self, strategy_name: str) -> list[dict]:
        holds: list[dict] = []
        for position in self._ledger.positions.values():
            if position.strategy != strategy_name or position.qty <= 0:
                continue
            holds.append({
                "code": position.code,
                "qty": position.qty,
                "avg_price": position.avg_price,
                "evlu_amt": int(position.market_value_basis),
            })
        return holds

    def get_strategy_return_history(self, strategy_name: str) -> list[dict]:
        return []


def _state_file(state_dir: str, strategy_key: str, state_suffix: str = "") -> str:
    return os.path.join(state_dir, f"{strategy_key}_position_state{state_suffix}.json")


def _require_indicator_service(strategy_key: str, indicator_service: Any | None) -> Any:
    if indicator_service is None:
        raise ValueError(f"{strategy_key} 백테스트에는 indicator_service가 필요합니다.")
    return indicator_service


def _build_backtest_strategy(
    *,
    strategy_key: str,
    replay_sqs: Any,
    universe_service: Any,
    indicator_service: Any | None,
    backtest_clock: Any,
    state_dir: str,
    state_suffix: str = "",
    logger: logging.Logger | None = None,
    config: Any = None,
):
    """Build an active strategy with replay data and a pinned backtest clock.

    ``config`` is an optional strategy-specific config instance (e.g.
    ``OneilPocketPivotConfig``). When None, the strategy's default config is
    used. Ablation runs supply a config built from preset overrides.
    """
    strategy_logger = logger or logging.getLogger(f"backtest.{strategy_key}")

    if strategy_key == "oneil_pocket_pivot":
        from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy

        return OneilPocketPivotStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            market_clock=backtest_clock,
            logger=strategy_logger,
            state_file=_state_file(state_dir, strategy_key, state_suffix),
            config=config,
        )
    if strategy_key == "oneil_squeeze_breakout":
        from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy

        return OneilSqueezeBreakoutStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            market_clock=backtest_clock,
            logger=strategy_logger,
            state_file=_state_file(state_dir, strategy_key, state_suffix),
            config=config,
        )
    if strategy_key == "high_tight_flag":
        from strategies.high_tight_flag_strategy import HighTightFlagStrategy

        return HighTightFlagStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            market_clock=backtest_clock,
            logger=strategy_logger,
            state_file=_state_file(state_dir, strategy_key, state_suffix),
            config=config,
        )
    if strategy_key == "first_pullback":
        from strategies.first_pullback_strategy import FirstPullbackStrategy

        return FirstPullbackStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            market_clock=backtest_clock,
            logger=strategy_logger,
            state_file=_state_file(state_dir, strategy_key, state_suffix),
            config=config,
        )
    if strategy_key == "larry_williams_vbo":
        from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOStrategy

        return LarryWilliamsVBOStrategy(
            stock_query_service=replay_sqs,
            market_clock=backtest_clock,
            universe_service=universe_service,
            logger=strategy_logger,
            config=config,
        )
    if strategy_key == "rsi2_pullback":
        from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy

        return RSI2PullbackStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            indicator_service=_require_indicator_service(strategy_key, indicator_service),
            market_clock=backtest_clock,
            logger=strategy_logger,
            state_file=_state_file(state_dir, strategy_key, state_suffix),
            config=config,
        )
    if strategy_key == "larry_williams_channel_breakout":
        from strategies.larry_williams_channel_breakout_strategy import (
            LarryWilliamsChannelBreakoutStrategy,
        )

        return LarryWilliamsChannelBreakoutStrategy(
            stock_query_service=replay_sqs,
            universe_service=universe_service,
            indicator_service=_require_indicator_service(strategy_key, indicator_service),
            market_clock=backtest_clock,
            logger=strategy_logger,
            state_file=_state_file(state_dir, strategy_key, state_suffix),
            config=config,
        )

    raise ValueError(f"지원하지 않는 백테스트 전략입니다: {strategy_key}")


@dataclass(frozen=True)
class _RiskSizingServices:
    position_sizing_service: Any | None = None
    risk_gate_service: Any | None = None


def _build_risk_sizing_services(
    *,
    use_risk_sizing: bool,
    config: Any,
    ledger,
    indicator_service,
    logger,
) -> _RiskSizingServices:
    if not use_risk_sizing:
        return _RiskSizingServices()

    from services.position_sizing_service import PositionSizingService
    from services.risk_gate_service import RiskGateService

    snapshot_cache = _BacktestLedgerAccountSnapshotCache(ledger)
    risk_provider = _BacktestStrategyRiskProvider(ledger)
    risk_gate_config = getattr(config, "risk_gate", None)
    order_policy_config = getattr(config, "order_policy", None)

    position_sizing_service = PositionSizingService(
        account_snapshot_cache=snapshot_cache,
        indicator_service=indicator_service,
        config=getattr(config, "position_sizing"),
        logger=logger,
        risk_gate_config=risk_gate_config,
        quote_provider=None,
        order_policy_config=order_policy_config,
    )
    risk_gate_service = RiskGateService(
        config=risk_gate_config,
        kill_switch_service=None,
        account_snapshot_cache=snapshot_cache,
        strategy_risk_provider=risk_provider,
        logger=logger,
    )
    return _RiskSizingServices(
        position_sizing_service=position_sizing_service,
        risk_gate_service=risk_gate_service,
    )


def _format_console(result) -> str:
    buy_count = sum(1 for report in result.execution_reports if report.order.side.value == "BUY")
    sell_count = sum(1 for report in result.execution_reports if report.order.side.value == "SELL")
    rejected_count = len(result.journal_records)
    portfolio = result.portfolio or {}
    positions = portfolio.get("positions") or {}

    lines = [
        "[BACKTEST RESULT]",
        f"전략: {result.strategy_name}",
        f"기간: {result.dates[0]} ~ {result.dates[-1]} ({len(result.dates)}일)",
        f"BUY 체결: {buy_count}",
        f"SELL 체결: {sell_count}",
        f"거부 기록: {rejected_count}",
        f"보유 종목: {len(positions)}",
        f"현금: {portfolio.get('cash', 0):,.0f}",
        f"가용현금: {portfolio.get('available_cash', 0):,.0f}",
        f"실현손익(순): {portfolio.get('realized_net_pnl', 0):,.0f}",
    ]
    execution_bar_policy = getattr(result, "execution_bar_policy", "")
    if execution_bar_policy:
        lines.append(f"체결 봉 정책: {execution_bar_policy}")
    saved_run = getattr(result, "saved_journal_run", None) or {}
    if saved_run.get("run_id"):
        lines.append(f"journal run: {saved_run['run_id']}")
    monte_carlo = getattr(result, "monte_carlo", None)
    if monte_carlo:
        lines.extend(_format_monte_carlo_console_lines(monte_carlo))
    profitability_gate = getattr(result, "profitability_gate", None)
    if profitability_gate:
        lines.extend(_format_profitability_gate_console_lines(profitability_gate))
    ablation = getattr(result, "ablation", None)
    if ablation:
        lines.extend(
            _format_ablation_console_lines(ablation["strategy_key"], ablation["summary"])
        )
        if ablation.get("universe_exclusion"):
            lines.extend(
                _format_universe_exclusion_console_lines(
                    ablation["strategy_key"], ablation["universe_exclusion"]
                )
            )
    parameter_stability = getattr(result, "parameter_stability", None)
    if parameter_stability:
        lines.extend(
            _format_parameter_stability_console_lines(
                parameter_stability["strategy_key"], parameter_stability["summary"]
            )
        )
    return "\n".join(lines)


def _result_to_payload(result) -> dict[str, Any]:
    return {
        "strategy_name": result.strategy_name,
        "dates": result.dates,
        "execution_reports": [
            {
                "order_id": report.order.order_id,
                "code": report.order.code,
                "side": report.order.side.value,
                "qty": report.order.qty,
                "filled_qty": report.filled_qty,
                "fill_price": report.fill_price,
                "status": report.status.value,
                "reason": report.reason,
                "execution_bar_policy": getattr(report, "execution_bar_policy", ""),
            }
            for report in result.execution_reports
        ],
        "journal_records": result.journal_records,
        "portfolio": result.portfolio,
        "saved_journal_run": getattr(result, "saved_journal_run", {}),
        "execution_bar_policy": getattr(result, "execution_bar_policy", ""),
        "monte_carlo": getattr(result, "monte_carlo", None),
        "profitability_gate": getattr(result, "profitability_gate", None),
        "ablation": getattr(result, "ablation", None),
        "parameter_stability": getattr(result, "parameter_stability", None),
    }


def _format_json(result) -> str:
    payload = _result_to_payload(result)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_walk_forward_console(result) -> str:
    summary = result.summary or {}
    lines = [
        "[WALK-FORWARD BACKTEST RESULT]",
        f"구간 수: {summary.get('segment_count', 0)}",
        f"embargo 일수: {summary.get('embargo_days', 0)}",
        f"train 일수 합계: {summary.get('train_days', 0)}",
        f"tune 일수 합계: {summary.get('tune_days', 0)}",
        f"test 일수 합계: {summary.get('test_days', 0)}",
        f"검증 실현손익(순): {summary.get('test_realized_net_pnl', 0):,.0f}",
        f"검증 체결 수: {summary.get('test_execution_count', 0)}",
        f"검증 거부 기록: {summary.get('test_rejected_count', 0)}",
    ]
    monte_carlo = getattr(result, "monte_carlo", None)
    if monte_carlo:
        lines.extend(_format_monte_carlo_console_lines(monte_carlo))
    profitability_gate = getattr(result, "profitability_gate", None)
    if profitability_gate:
        lines.extend(_format_profitability_gate_console_lines(profitability_gate))
    return "\n".join(lines)


def _format_walk_forward_json(result) -> str:
    payload = {
        "summary": result.summary,
        "monte_carlo": getattr(result, "monte_carlo", None),
        "profitability_gate": getattr(result, "profitability_gate", None),
        "segments": [
            {
                "index": segment.index,
                "train_dates": segment.train_dates,
                "tune_dates": segment.tune_dates,
                "test_dates": segment.test_dates,
                "train_result": _result_to_payload(segment.train_result),
                "tune_result": _result_to_payload(segment.tune_result),
                "test_result": _result_to_payload(segment.test_result),
            }
            for segment in result.segments
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_profitability_gate_console_lines(result: dict[str, Any]) -> list[str]:
    lines = ["", "[PROFITABILITY GATE]"]
    summary = result.get("summary") or {}
    lines.append(
        "통과 {pass_count}, 실패 {fail_count}, 표본부족 {insufficient}".format(
            pass_count=summary.get("pass_count", 0),
            fail_count=summary.get("fail_count", 0),
            insufficient=summary.get("insufficient_sample_count", 0),
        )
    )
    warnings = result.get("warnings") or []
    if warnings:
        lines.append(f"warnings: {', '.join(warnings)}")
    bias = result.get("multiple_testing_bias") or {}
    if bias.get("bias_warning"):
        ratio = bias.get("top_to_median_ratio")
        ratio_str = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else "n/a"
        lines.append(
            "multiple-testing trials={trial_count} best={best} top/median={ratio}".format(
                trial_count=bias.get("trial_count", 0),
                best=bias.get("best_strategy") or "n/a",
                ratio=ratio_str,
            )
        )
    correlation = result.get("strategy_correlation") or {}
    max_pair = correlation.get("max_positive_pair") or {}
    if max_pair and (
        correlation.get("warnings")
        or "strategy_correlation_high" in warnings
    ):
        corr = max_pair.get("correlation")
        corr_str = f"{corr:.2f}" if isinstance(corr, (int, float)) else "n/a"
        lines.append(
            "strategy-correlation max={left}/{right} corr={corr} overlap={overlap}".format(
                left=max_pair.get("left") or "n/a",
                right=max_pair.get("right") or "n/a",
                corr=corr_str,
                overlap=max_pair.get("overlap", 0),
            )
        )
    market_beta = result.get("market_beta") or {}
    if market_beta and (
        market_beta.get("warnings")
        or "portfolio_market_beta_high" in warnings
        or "strategy_market_beta_high" in warnings
    ):
        threshold = market_beta.get("warning_threshold")
        threshold_str = f"{threshold:.2f}" if isinstance(threshold, (int, float)) else "n/a"
        portfolio_beta = (market_beta.get("portfolio") or {}).get("beta")
        if portfolio_beta is not None:
            lines.append(
                "market-beta portfolio beta={beta} overlap={overlap} threshold={threshold}".format(
                    beta=f"{portfolio_beta:.2f}" if isinstance(portfolio_beta, (int, float)) else "n/a",
                    overlap=(market_beta.get("portfolio") or {}).get("overlap", 0),
                    threshold=threshold_str,
                )
            )
        for item in market_beta.get("high_beta_strategies") or []:
            beta = item.get("beta")
            lines.append(
                "market-beta strategy={strategy} beta={beta} overlap={overlap} threshold={threshold}".format(
                    strategy=item.get("strategy") or "n/a",
                    beta=f"{beta:.2f}" if isinstance(beta, (int, float)) else "n/a",
                    overlap=item.get("overlap", 0),
                    threshold=threshold_str,
                )
            )
    entry_pressure = result.get("entry_pressure") or {}
    if entry_pressure and (
        entry_pressure.get("warnings")
        or "portfolio_daily_entry_pressure_high" in warnings
    ):
        lines.append(
            "entry-pressure max_date={date} entries={count} threshold={threshold}".format(
                date=entry_pressure.get("max_daily_entry_date") or "n/a",
                count=entry_pressure.get("max_daily_entry_count", 0),
                threshold=entry_pressure.get("daily_entry_warning_threshold", 0),
            )
        )
        intraday_windows = entry_pressure.get("intraday_windows") or {}
        for window_name in ("opening", "closing"):
            window = intraday_windows.get(window_name) or {}
            threshold = int(window.get("entry_warning_threshold") or 0)
            count = int(window.get("max_entry_count") or 0)
            if threshold > 0 and count >= threshold:
                lines.append(
                    "entry-pressure {window} max_date={date} entries={count} "
                    "threshold={threshold}".format(
                        window=window_name,
                        date=window.get("max_entry_date") or "n/a",
                        count=count,
                        threshold=threshold,
                    )
                )
    cooldown = result.get("cooldown") or {}
    if cooldown and (
        cooldown.get("warnings")
        or "portfolio_consecutive_loss_cooldown_candidate" in warnings
    ):
        threshold = cooldown.get("consecutive_loss_warning_threshold", 0)
        for candidate in cooldown.get("candidates") or []:
            lines.append(
                "cooldown-candidate strategy={strategy} losses={losses} "
                "current={current} threshold={threshold} latest={latest}".format(
                    strategy=candidate.get("strategy") or "n/a",
                    losses=candidate.get("max_consecutive_losses", 0),
                    current=candidate.get("current_consecutive_losses", 0),
                    threshold=threshold,
                    latest=candidate.get("latest_loss_date") or "n/a",
                )
            )
    for strategy, item in sorted((result.get("strategies") or {}).items()):
        reasons = item.get("blocking_reasons") or []
        warnings = item.get("warnings") or []
        suffix = f" ({', '.join(reasons)})" if reasons else ""
        warning_suffix = f" [warn: {', '.join(warnings)}]" if warnings else ""
        lines.append(f"{strategy}: {item.get('status')}{suffix}{warning_suffix}")
    return lines


def _format_monte_carlo_console_lines(summary: dict[str, Any]) -> list[str]:
    return [
        "",
        "[MONTE CARLO]",
        f"Monte Carlo 거래 수: {summary.get('trade_count', 0)}",
        f"Monte Carlo runs: {summary.get('runs', 0)}",
        f"최악 MDD: {summary.get('worst_max_drawdown_pct', 0):.2f}%",
        f"최장 연속 손실: {summary.get('worst_losing_streak', 0)}",
        f"ruin probability: {summary.get('ruin_probability', 0) * 100:.2f}%",
    ]


def _run_monte_carlo_for_result(result, args: argparse.Namespace) -> None:
    from services.backtest_monte_carlo import (
        BacktestMonteCarloConfig,
        BacktestMonteCarloSimulator,
        extract_net_pnls_from_journal,
    )

    trade_net_pnls = extract_net_pnls_from_journal(result.journal_records)
    object.__setattr__(result, "monte_carlo", BacktestMonteCarloSimulator(
        BacktestMonteCarloConfig(
            runs=args.mc_runs,
            seed=args.mc_seed,
            initial_capital=args.initial_cash,
            ruin_drawdown_pct=args.mc_ruin_drawdown_pct,
        )
    ).run(trade_net_pnls).to_dict())


def _run_monte_carlo_for_walk_forward(result, args: argparse.Namespace) -> None:
    from services.backtest_monte_carlo import (
        BacktestMonteCarloConfig,
        BacktestMonteCarloSimulator,
        extract_net_pnls_from_journal,
    )

    trade_net_pnls: list[float] = []
    for segment in result.segments:
        trade_net_pnls.extend(
            extract_net_pnls_from_journal(segment.test_result.journal_records)
        )
    object.__setattr__(result, "monte_carlo", BacktestMonteCarloSimulator(
        BacktestMonteCarloConfig(
            runs=args.mc_runs,
            seed=args.mc_seed,
            initial_capital=args.initial_cash,
            ruin_drawdown_pct=args.mc_ruin_drawdown_pct,
        )
    ).run(trade_net_pnls).to_dict())


def _run_profitability_gate_for_result(result, app_config: Any, *, initial_cash: float) -> None:
    from services.strategy_profitability_gate_service import evaluate_strategy_profitability_gate

    gate_config = _build_profitability_gate_config(app_config, initial_cash=initial_cash)
    object.__setattr__(
        result,
        "profitability_gate",
        evaluate_strategy_profitability_gate(
            getattr(result, "journal_records", []) or [],
            gate_config,
            monte_carlo=getattr(result, "monte_carlo", None),
            parameter_stability=getattr(result, "parameter_stability", None),
            ablation=getattr(result, "ablation", None),
        ),
    )


def _run_profitability_gate_for_walk_forward(result, app_config: Any, *, initial_cash: float) -> None:
    from services.strategy_profitability_gate_service import evaluate_strategy_profitability_gate

    records: list[dict] = []
    for segment in getattr(result, "segments", []) or []:
        records.extend(getattr(segment.test_result, "journal_records", []) or [])
    gate_config = _build_profitability_gate_config(app_config, initial_cash=initial_cash)
    object.__setattr__(
        result,
        "profitability_gate",
        evaluate_strategy_profitability_gate(
            records,
            gate_config,
            monte_carlo=getattr(result, "monte_carlo", None),
            validation_metrics_by_strategy=(getattr(result, "summary", {}) or {}).get(
                "validation_metrics_by_strategy"
            ),
        ),
    )


def _build_profitability_gate_config(app_config: Any, *, initial_cash: float):
    from services.strategy_profitability_gate_service import StrategyProfitabilityGateConfig

    raw = getattr(app_config, "strategy_profitability_gate", None)
    if isinstance(raw, StrategyProfitabilityGateConfig):
        values = {
            field: getattr(raw, field)
            for field in StrategyProfitabilityGateConfig.__dataclass_fields__
        }
    elif hasattr(raw, "model_dump"):
        values = raw.model_dump()
    elif isinstance(raw, dict):
        values = dict(raw)
    elif raw is not None:
        values = {
            field: getattr(raw, field)
            for field in StrategyProfitabilityGateConfig.__dataclass_fields__
            if hasattr(raw, field)
        }
    else:
        values = {}

    if values.get("capital_base_won") is None:
        values["capital_base_won"] = initial_cash
    allowed = StrategyProfitabilityGateConfig.__dataclass_fields__
    return StrategyProfitabilityGateConfig(**{k: v for k, v in values.items() if k in allowed})


def _build_default_strategy_config(strategy_key: str) -> Any:
    """Return a fresh strategy-specific default config instance."""
    if strategy_key == "oneil_pocket_pivot":
        from strategies.oneil_common_types import OneilPocketPivotConfig

        return OneilPocketPivotConfig()
    if strategy_key == "oneil_squeeze_breakout":
        from strategies.oneil_common_types import OneilBreakoutConfig

        return OneilBreakoutConfig()
    if strategy_key == "high_tight_flag":
        from strategies.oneil_common_types import HTFConfig

        return HTFConfig()
    if strategy_key == "first_pullback":
        from strategies.first_pullback_types import FirstPullbackConfig

        return FirstPullbackConfig()
    if strategy_key == "larry_williams_vbo":
        from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOConfig

        return LarryWilliamsVBOConfig()
    if strategy_key == "rsi2_pullback":
        from strategies.rsi2_pullback_types import RSI2PullbackConfig

        return RSI2PullbackConfig()
    if strategy_key == "larry_williams_channel_breakout":
        from strategies.larry_williams_cb_types import LarryWilliamsCBConfig

        return LarryWilliamsCBConfig()
    raise ValueError(
        f"Ablation default config not defined for strategy '{strategy_key}'."
    )


def _build_ablation_overrides(
    *,
    strategy_key: str,
    base_universe: Any,
    variant: Any,
) -> tuple[Any, Any]:
    """Return ``(universe, config)`` for the given variant, or pass-throughs.

    When ``variant`` is None, returns ``(base_universe, None)`` so the baseline
    path is unchanged. When the variant has ``force_market_timing_ok=True``,
    the universe is wrapped. When the variant has ``config_overrides``, a
    fresh strategy-specific default config is built and overrides are applied.
    """
    if variant is None:
        return base_universe, None
    from services.strategy_ablation_service import (
        ForceMarketTimingOkUniverseWrapper,
        apply_config_overrides,
    )

    universe = base_universe
    universe_type = variant.universe_overrides.get("universe_type")
    if universe_type == "generic_liquidity":
        from services.generic_liquidity_universe_service import (
            GenericLiquidityUniverseService,
        )

        kwargs: dict[str, Any] = {
            "sqs": base_universe._sqs,
            "time_manager": base_universe._tm,
            "market_regime_service": getattr(base_universe, "_regime_svc", None),
        }
        for key in (
            "min_avg_trading_value_5d",
            "min_market_cap",
            "max_watchlist",
        ):
            if key in variant.universe_overrides:
                kwargs[key] = variant.universe_overrides[key]
        universe = GenericLiquidityUniverseService(**kwargs)
    elif universe_type == "rsi2_mean_reversion":
        from services.rsi2_mean_reversion_universe_service import (
            Rsi2MeanReversionUniverseService,
        )

        kwargs = {
            "sqs": base_universe._sqs,
            "time_manager": base_universe._tm,
            "market_regime_service": getattr(base_universe, "_regime_svc", None),
        }
        for key in (
            "min_avg_trading_value_5d",
            "min_market_cap",
            "min_volatility_20d_annualized",
            "max_watchlist",
        ):
            if key in variant.universe_overrides:
                kwargs[key] = variant.universe_overrides[key]
        universe = Rsi2MeanReversionUniverseService(**kwargs)
    elif universe_type == "vbo_volatility":
        from services.vbo_volatility_universe_service import (
            VboVolatilityUniverseService,
        )

        kwargs = {
            "sqs": base_universe._sqs,
            "time_manager": base_universe._tm,
            "market_regime_service": getattr(base_universe, "_regime_svc", None),
        }
        for key in (
            "min_avg_trading_value_5d",
            "min_market_cap",
            "min_volatility_20d_annualized",
            "max_watchlist",
        ):
            if key in variant.universe_overrides:
                kwargs[key] = variant.universe_overrides[key]
        universe = VboVolatilityUniverseService(**kwargs)
    if variant.universe_overrides.get("force_market_timing_ok"):
        universe = ForceMarketTimingOkUniverseWrapper(universe)

    config = None
    if variant.config_overrides:
        config = apply_config_overrides(
            _build_default_strategy_config(strategy_key), variant.config_overrides
        )
    return universe, config


def _resolve_ablation_preset(strategy_key: str):
    if strategy_key == "oneil_pocket_pivot":
        from strategies.oneil_pocket_pivot_ablation import (
            ONEIL_POCKET_PIVOT_ABLATION_PRESET,
        )
        return ONEIL_POCKET_PIVOT_ABLATION_PRESET
    if strategy_key == "oneil_squeeze_breakout":
        from strategies.oneil_squeeze_breakout_ablation import (
            ONEIL_SQUEEZE_BREAKOUT_ABLATION_PRESET,
        )
        return ONEIL_SQUEEZE_BREAKOUT_ABLATION_PRESET
    if strategy_key == "high_tight_flag":
        from strategies.high_tight_flag_ablation import (
            HIGH_TIGHT_FLAG_ABLATION_PRESET,
        )
        return HIGH_TIGHT_FLAG_ABLATION_PRESET
    if strategy_key == "first_pullback":
        from strategies.first_pullback_ablation import (
            FIRST_PULLBACK_ABLATION_PRESET,
        )
        return FIRST_PULLBACK_ABLATION_PRESET
    if strategy_key == "larry_williams_vbo":
        from strategies.larry_williams_vbo_ablation import (
            LARRY_WILLIAMS_VBO_ABLATION_PRESET,
        )
        return LARRY_WILLIAMS_VBO_ABLATION_PRESET
    if strategy_key == "rsi2_pullback":
        from strategies.rsi2_pullback_ablation import (
            RSI2_PULLBACK_ABLATION_PRESET,
        )
        return RSI2_PULLBACK_ABLATION_PRESET
    if strategy_key == "larry_williams_channel_breakout":
        from strategies.larry_williams_channel_breakout_ablation import (
            LARRY_WILLIAMS_CHANNEL_BREAKOUT_ABLATION_PRESET,
        )
        return LARRY_WILLIAMS_CHANNEL_BREAKOUT_ABLATION_PRESET
    raise ValueError(
        f"Ablation preset 이 정의되지 않은 전략입니다: '{strategy_key}'."
    )


def _filter_ablation_variants(preset, names: Optional[str]):
    if not names:
        return preset.variants
    name_set = {n.strip() for n in str(names).split(",") if n.strip()}
    known = {v.name for v in preset.variants}
    unknown = name_set - known
    if unknown:
        raise ValueError(
            f"{preset.strategy_key} 에 정의되지 않은 ablation variant: {sorted(unknown)}. "
            f"사용 가능: {sorted(known)}"
        )
    # Preserve preset variant order (deterministic)
    return tuple(v for v in preset.variants if v.name in name_set)


async def _run_ablation_for_result(
    result,
    args: argparse.Namespace,
    *,
    run_variant_fn: Callable[[Any], Awaitable[Any]],
) -> None:
    """Run each variant via ``run_variant_fn`` and attach summary to ``result``.

    ``run_variant_fn`` is supplied by the script's ``_run`` so the variant runner
    can reuse the same dates, ledger, simulator, and replay context. Tests stub
    it with an async function returning a ``BacktestPeriodRunResult``.
    """
    if not getattr(args, "ablation", None):
        return
    from services.strategy_ablation_service import (
        compute_ablation_gate_summary,
        compute_ablation_summary,
        compute_universe_exclusion_summary,
    )

    preset = _resolve_ablation_preset(args.ablation)
    variants = _filter_ablation_variants(preset, getattr(args, "ablation_variants", None))

    variant_records: dict[str, list[dict]] = {}
    for variant in variants:
        variant_result = await run_variant_fn(variant)
        variant_records[variant.name] = list(
            getattr(variant_result, "journal_records", []) or []
        )

    baseline_records = list(getattr(result, "journal_records", []) or [])
    summary = compute_ablation_summary(
        baseline_records=baseline_records,
        variant_records=variant_records,
        capital_base_won=float(getattr(args, "initial_cash", 0.0)) or None,
    )
    gate = compute_ablation_gate_summary(summary)
    exclusion = compute_universe_exclusion_summary(
        baseline_records=baseline_records,
        variant_records=variant_records,
    )
    object.__setattr__(
        result,
        "ablation",
        {
            "strategy_key": preset.strategy_key,
            "summary": summary,
            "gate": gate,
            "universe_exclusion": exclusion,
        },
    )


def _format_ablation_console_lines(strategy_key: str, summary: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append(f"[ABLATION] strategy={strategy_key}")
    baseline_metrics = summary.get("baseline", {}).get("metrics", {})
    lines.append(_format_ablation_row("baseline", baseline_metrics, delta=None))
    for variant_name, payload in summary.get("variants", {}).items():
        lines.append(
            _format_ablation_row(
                variant_name,
                payload.get("metrics", {}),
                delta=payload.get("delta"),
            )
        )
    return lines


def _format_universe_exclusion_console_lines(
    strategy_key: str, exclusion: dict[str, Any]
) -> list[str]:
    """Render the universe-exclusion summary attached by ``_run_ablation_for_result``.

    Shows, per variant, how many codes were captured outside the baseline traded
    set and the aggregate net_pnl on those codes. Useful when comparing universes
    (e.g. Oneil vs generic liquidity) to see what the baseline universe missed.
    """
    lines: list[str] = []
    variants = exclusion.get("variants") or {}
    if not variants:
        return lines
    lines.append(f"[UNIVERSE_EXCLUSION] strategy={strategy_key}")
    lines.append(
        f"  baseline_traded_codes={len(exclusion.get('baseline_codes') or [])}"
    )
    for variant_name, payload in variants.items():
        variant_only = payload.get("variant_only_codes") or []
        baseline_only = payload.get("baseline_only_codes") or []
        shared = payload.get("shared_codes") or []
        agg = payload.get("variant_only_summary") or {}
        trade_count = int(agg.get("trade_count", 0))
        total_pnl = float(agg.get("total_net_pnl", 0.0))
        win = int(agg.get("win_count", 0))
        loss = int(agg.get("loss_count", 0))
        lines.append(
            f"  {variant_name:<28} "
            f"variant_only={len(variant_only):>3} "
            f"baseline_only={len(baseline_only):>3} "
            f"shared={len(shared):>3} "
            f"variant_only_trades={trade_count:>3} "
            f"variant_only_net_pnl={total_pnl:>12,.0f} "
            f"W/L={win}/{loss}"
        )
    return lines


def _format_ablation_row(
    name: str,
    metrics: dict[str, Any],
    delta: Optional[dict[str, Any]],
) -> str:
    pf = metrics.get("profit_factor")
    payoff = metrics.get("payoff_ratio")
    pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else "n/a"
    payoff_str = f"{payoff:.2f}" if isinstance(payoff, (int, float)) else "n/a"
    base = (
        f"  {name:<28} trades={int(metrics.get('trade_count', 0)):>3} "
        f"win_rate={float(metrics.get('win_rate', 0.0)):.2%} "
        f"avg_ret={float(metrics.get('avg_net_return', 0.0)):.3f} "
        f"net_pnl={float(metrics.get('total_net_pnl', 0.0)):>12,.0f} "
        f"pf={pf_str} payoff={payoff_str} "
        f"mdd={float(metrics.get('mdd_amount', 0.0)):>12,.0f}"
    )
    if not delta:
        return base
    pnl_diff = float(delta.get("total_net_pnl_diff", 0.0))
    trade_diff = int(delta.get("trade_count_diff", 0))
    return base + f"  Δtrades={trade_diff:+d} Δnet_pnl={pnl_diff:+,.0f}"


def _resolve_parameter_stability_preset(strategy_key: str):
    if strategy_key == "oneil_pocket_pivot":
        from strategies.oneil_pocket_pivot_parameter_stability import (
            ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET,
        )
        return ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET
    if strategy_key == "oneil_squeeze_breakout":
        from strategies.oneil_squeeze_breakout_parameter_stability import (
            ONEIL_SQUEEZE_BREAKOUT_PARAMETER_STABILITY_PRESET,
        )
        return ONEIL_SQUEEZE_BREAKOUT_PARAMETER_STABILITY_PRESET
    if strategy_key == "high_tight_flag":
        from strategies.high_tight_flag_parameter_stability import (
            HIGH_TIGHT_FLAG_PARAMETER_STABILITY_PRESET,
        )
        return HIGH_TIGHT_FLAG_PARAMETER_STABILITY_PRESET
    if strategy_key == "first_pullback":
        from strategies.first_pullback_parameter_stability import (
            FIRST_PULLBACK_PARAMETER_STABILITY_PRESET,
        )
        return FIRST_PULLBACK_PARAMETER_STABILITY_PRESET
    if strategy_key == "larry_williams_vbo":
        from strategies.larry_williams_vbo_parameter_stability import (
            LARRY_WILLIAMS_VBO_PARAMETER_STABILITY_PRESET,
        )
        return LARRY_WILLIAMS_VBO_PARAMETER_STABILITY_PRESET
    if strategy_key == "rsi2_pullback":
        from strategies.rsi2_pullback_parameter_stability import (
            RSI2_PULLBACK_PARAMETER_STABILITY_PRESET,
        )
        return RSI2_PULLBACK_PARAMETER_STABILITY_PRESET
    if strategy_key == "larry_williams_channel_breakout":
        from strategies.larry_williams_channel_breakout_parameter_stability import (
            LARRY_WILLIAMS_CHANNEL_BREAKOUT_PARAMETER_STABILITY_PRESET,
        )
        return LARRY_WILLIAMS_CHANNEL_BREAKOUT_PARAMETER_STABILITY_PRESET
    raise ValueError(
        f"Parameter stability preset 이 정의되지 않은 전략입니다: '{strategy_key}'."
    )


def _filter_parameter_stability_dimensions(preset, names: Optional[str]):
    if not names:
        return preset.dimensions
    name_set = {n.strip() for n in str(names).split(",") if n.strip()}
    known = {d.name for d in preset.dimensions}
    unknown = name_set - known
    if unknown:
        raise ValueError(
            f"{preset.strategy_key} 에 정의되지 않은 parameter stability dimension: "
            f"{sorted(unknown)}. 사용 가능: {sorted(known)}"
        )
    return tuple(d for d in preset.dimensions if d.name in name_set)


async def _run_parameter_stability_for_result(
    result,
    args: argparse.Namespace,
    *,
    run_variant_fn: Callable[[Any], Awaitable[Any]],
) -> None:
    """Sweep each dimension's values via ``run_variant_fn`` and attach the
    parameter-stability summary to ``result``.

    Each sweep point is sent through ``run_variant_fn`` as a synthesized
    ``AblationVariant(name=f"{dim.name}={value}", config_overrides={dim.parameter: value})``
    so the existing variant runner (which already accepts an ``AblationVariant``
    with ``config_overrides``) handles the per-point config replace and state
    suffix without further changes.
    """
    if not getattr(args, "parameter_stability", None):
        return
    from services.parameter_stability_service import compute_stability_summary
    from services.strategy_ablation_service import AblationVariant

    preset = _resolve_parameter_stability_preset(args.parameter_stability)
    dimensions = _filter_parameter_stability_dimensions(
        preset, getattr(args, "parameter_stability_dimensions", None)
    )

    sweep_records_by_dim: dict[str, dict[Any, list[dict]]] = {}
    for dim in dimensions:
        per_value: dict[Any, list[dict]] = {}
        for value in dim.values:
            variant = AblationVariant(
                name=f"{dim.name}={value}",
                description=f"Parameter stability sweep: {dim.parameter}={value}",
                config_overrides={dim.parameter: value},
            )
            variant_result = await run_variant_fn(variant)
            per_value[value] = list(
                getattr(variant_result, "journal_records", []) or []
            )
        sweep_records_by_dim[dim.name] = per_value

    summary = compute_stability_summary(
        baseline_records=list(getattr(result, "journal_records", []) or []),
        dimensions=dimensions,
        sweep_records_by_dim=sweep_records_by_dim,
        capital_base_won=float(getattr(args, "initial_cash", 0.0)) or None,
    )
    object.__setattr__(
        result,
        "parameter_stability",
        {"strategy_key": preset.strategy_key, "summary": summary},
    )


def _format_parameter_stability_console_lines(
    strategy_key: str, summary: dict[str, Any]
) -> list[str]:
    lines: list[str] = [f"[PARAMETER_STABILITY] strategy={strategy_key}"]
    baseline_metrics = summary.get("baseline", {}).get("metrics", {})
    lines.append(_format_parameter_stability_row("baseline", baseline_metrics, delta=None))
    for dim_name, payload in summary.get("dimensions", {}).items():
        baseline_value = payload.get("baseline_value")
        lines.append(f"  dim={dim_name} (baseline={baseline_value})")
        for point in payload.get("points", []):
            label = f"{point['value']}"
            if point.get("is_baseline"):
                label = f"* {label}"
            lines.append(
                _format_parameter_stability_row(
                    label, point.get("metrics", {}), delta=point.get("delta")
                )
            )
        stability = payload.get("stability", {})
        flag = stability.get("flag", "?")
        ratio = stability.get("ratio_vs_neighbors_avg")
        drop = stability.get("neighbor_drop_pct")
        reason = stability.get("reason", "")
        ratio_str = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else "n/a"
        drop_str = f"{drop:.1f}%" if isinstance(drop, (int, float)) else "n/a"
        lines.append(
            f"    -> stability={flag} ratio={ratio_str} max_neighbor_drop={drop_str} ({reason})"
        )
    return lines


def _format_parameter_stability_row(
    label: str,
    metrics: dict[str, Any],
    delta: Optional[dict[str, Any]],
) -> str:
    pf = metrics.get("profit_factor")
    payoff = metrics.get("payoff_ratio")
    pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else "n/a"
    payoff_str = f"{payoff:.2f}" if isinstance(payoff, (int, float)) else "n/a"
    base = (
        f"    {label:<22} trades={int(metrics.get('trade_count', 0)):>3} "
        f"win_rate={float(metrics.get('win_rate', 0.0)):.2%} "
        f"avg_ret={float(metrics.get('avg_net_return', 0.0)):.3f} "
        f"net_pnl={float(metrics.get('total_net_pnl', 0.0)):>12,.0f} "
        f"pf={pf_str} payoff={payoff_str}"
    )
    if not delta:
        return base
    pnl_diff = float(delta.get("total_net_pnl_diff", 0.0))
    trade_diff = int(delta.get("trade_count_diff", 0))
    return base + f"  Δtrades={trade_diff:+d} Δnet_pnl={pnl_diff:+,.0f}"


async def _run(args: argparse.Namespace) -> None:
    from scripts._bootstrap import bootstrap_pp_strategy, make_stdout_logger
    from config.config_loader import load_configs
    from repositories.backtest_journal_repository import BacktestJournalRepository
    from services.backtest_execution_simulator import BacktestPortfolioLedger
    from services.backtest_period_runner import BacktestPeriodRunner, BacktestPeriodRunnerConfig
    from services.backtest_replay_context import (
        BacktestMarketClock,
        apply_backtest_snapshot_context,
    )
    from services.backtest_replay_adapter import (
        StockQueryBacktestReplayService,
    )
    from services.backtest_walk_forward import (
        BacktestWalkForwardConfig,
        BacktestWalkForwardRunner,
    )

    dates = _build_dates(args)
    app_config = load_configs()
    bootstrap_logger = make_stdout_logger("backtest_bootstrap", level=logging.WARNING)
    print(f"[INFO] 서비스 초기화 중... (모의투자={args.paper})")
    try:
        sqs, universe_service, market_clock = await bootstrap_pp_strategy(
            is_paper_trading=args.paper,
            logger=bootstrap_logger,
        )
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    replay_sqs = StockQueryBacktestReplayService(
        sqs,
        program_provider=_get_program_provider(sqs),
    )
    backtest_clock = BacktestMarketClock.from_clock(
        market_clock,
        default_time=args.backtest_time,
    )
    apply_backtest_snapshot_context(
        universe_service,
        stock_query_service=replay_sqs,
        market_clock=backtest_clock,
    )
    bar_provider, mtm_bar_provider = _build_replay_bar_providers(replay_sqs)
    indicator_service = getattr(sqs, "indicator_service", None)
    with tempfile.TemporaryDirectory(prefix="period_backtest_") as tmp_dir:
        def make_runner(
            *,
            phase: str | None = None,
            segment=None,
            phase_dates: list[str] | None = None,
            variant=None,
        ) -> BacktestPeriodRunner:
            ledger = BacktestPortfolioLedger(initial_cash=args.initial_cash)
            risk_sizing = _build_risk_sizing_services(
                use_risk_sizing=args.use_risk_sizing,
                config=app_config,
                ledger=ledger,
                indicator_service=indicator_service,
                logger=bootstrap_logger,
            )
            state_suffix = f"_{segment.index}_{phase}" if segment is not None else ""
            if variant is not None:
                state_suffix = f"{state_suffix}_ablation_{variant.name}"
            variant_universe, variant_config = _build_ablation_overrides(
                strategy_key=args.strategy,
                base_universe=universe_service,
                variant=variant,
            )
            strategy = _build_backtest_strategy(
                strategy_key=args.strategy,
                replay_sqs=replay_sqs,
                universe_service=variant_universe,
                indicator_service=indicator_service,
                backtest_clock=backtest_clock,
                state_dir=tmp_dir,
                state_suffix=state_suffix,
                logger=logging.getLogger(f"backtest.{args.strategy}"),
                config=variant_config,
            )
            max_positions = (
                {strategy.name: args.max_positions}
                if args.max_positions is not None
                else None
            )
            target_dates = phase_dates or dates
            run_prefix = "wf" if segment is not None else "period"
            run_parts = [run_prefix]
            if segment is not None:
                run_parts.extend([str(segment.index), str(phase)])
            run_parts.extend([strategy.name, target_dates[0], target_dates[-1]])
            metadata = {
                "cli": "scripts.run_backtest",
                "initial_cash": args.initial_cash,
                "max_positions": args.max_positions,
                "strategy_key": args.strategy,
                "backtest_time": args.backtest_time,
                "execution_bar_policy": args.execution_bar_policy,
                "use_risk_sizing": args.use_risk_sizing,
                "output": args.output,
                "walk_forward": segment is not None,
            }
            if segment is not None:
                metadata.update(
                    {
                        "walk_forward_phase": phase,
                        "walk_forward_segment": segment.index,
                        "train_dates": segment.train_dates,
                        "tune_dates": segment.tune_dates,
                        "test_dates": segment.test_dates,
                    }
                )
            return BacktestPeriodRunner(
                strategy=strategy,
                bar_provider=bar_provider,
                ledger=ledger,
                backtest_journal_repository=BacktestJournalRepository(),
                run_id="_".join(run_parts),
                metadata=metadata,
                config=BacktestPeriodRunnerConfig(
                    max_positions_per_strategy=max_positions,
                    execution_bar_policy=args.execution_bar_policy,
                ),
                position_sizing_service=risk_sizing.position_sizing_service,
                risk_gate_service=risk_sizing.risk_gate_service,
                date_context_targets=[backtest_clock, replay_sqs],
                mtm_bar_provider=mtm_bar_provider,
            )

        if args.walk_forward:
            config = BacktestWalkForwardConfig(
                train_size=args.wf_train_days,
                tune_size=args.wf_tune_days,
                test_size=args.wf_test_days,
                step_size=args.wf_step_days,
                embargo_days=args.wf_embargo_days,
            )

            def runner_factory(phase: str, segment):
                phase_dates = getattr(segment, f"{phase}_dates")
                return make_runner(phase=phase, segment=segment, phase_dates=phase_dates)

            print("[INFO] walk-forward 백테스트 실행 중...\n")
            result = await BacktestWalkForwardRunner(
                runner_factory=runner_factory,
                config=config,
            ).run(dates)
            if args.monte_carlo:
                _run_monte_carlo_for_walk_forward(result, args)
            if args.profitability_gate:
                _run_profitability_gate_for_walk_forward(result, app_config, initial_cash=args.initial_cash)
            rendered = (
                _format_walk_forward_json(result)
                if args.output == "json"
                else _format_walk_forward_console(result)
            )
        else:
            print("[INFO] 기간 백테스트 실행 중...\n")
            result = await make_runner().run(dates)
            if args.monte_carlo:
                _run_monte_carlo_for_result(result, args)
            if args.ablation:
                if args.strategy != args.ablation:
                    raise ValueError(
                        f"--ablation({args.ablation}) 과 --strategy({args.strategy}) 가 다릅니다. "
                        "ablation 은 baseline 과 같은 전략에 대해서만 의미가 있습니다."
                    )

                async def _variant_runner(variant):
                    print(f"[INFO] ablation variant 실행: {variant.name}")
                    return await make_runner(variant=variant).run(dates)

                await _run_ablation_for_result(
                    result, args, run_variant_fn=_variant_runner
                )
            if args.parameter_stability:
                if args.strategy != args.parameter_stability:
                    raise ValueError(
                        f"--parameter-stability({args.parameter_stability}) 과 "
                        f"--strategy({args.strategy}) 가 다릅니다. parameter stability 는 "
                        "baseline 과 같은 전략에 대해서만 의미가 있습니다."
                    )

                async def _stability_variant_runner(variant):
                    print(f"[INFO] parameter-stability sweep 실행: {variant.name}")
                    return await make_runner(variant=variant).run(dates)

                await _run_parameter_stability_for_result(
                    result, args, run_variant_fn=_stability_variant_runner
                )
            if args.profitability_gate:
                _run_profitability_gate_for_result(result, app_config, initial_cash=args.initial_cash)
            rendered = _format_json(result) if args.output == "json" else _format_console(result)

    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as fp:
            fp.write(rendered)
        print(f"[INFO] 결과 저장: {args.output_file}")
    else:
        print(rendered)


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
