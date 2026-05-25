"""Pre-deploy operational checks.

배포 직전 운영 환경의 상태를 점검하는 서비스. 각 항목은 독립적으로 실행 가능하며,
`run_all()` 은 모든 검사를 끝까지 수행한 뒤 요약을 돌려준다. 자세한 운영 절차는
`docs/operations_runbook.md` 의 *배포 체크리스트* 참고.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

from common.config_hashing import compute_config_hash


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    WARN = "WARN"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""
    elapsed_ms: int = 0


@dataclass
class PreDeployCheckSummary:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def has_failure(self) -> bool:
        return any(r.status == CheckStatus.FAIL for r in self.results)

    @property
    def counts(self) -> dict:
        out = {s: 0 for s in CheckStatus}
        for r in self.results:
            out[r.status] += 1
        return {s.value: c for s, c in out.items()}


# 알려진 한투 base URL prefix. paper(모의) / real(실전) 가 섞이지 않도록 단순 일치 검사를 한다.
_PAPER_HOST_HINTS = ("openapivts", "vts.koreainvestment", "31000")
_REAL_HOST_HINTS = ("openapi.koreainvestment", "ops.koreainvestment", "21000")


def _looks_like_paper(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _PAPER_HOST_HINTS)


def _looks_like_real(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _REAL_HOST_HINTS)


class PreDeployCheckService:
    """배포 직전 운영 점검 서비스.

    의존성은 모두 주입한다. live broker 가 필요한 점검(WebSocket/account snapshot)은
    `offline=True` 모드에서 자동으로 SKIPPED 처리된다.
    """

    def __init__(
        self,
        *,
        config_loader: Callable[[], Any],
        env_provider: Optional[Callable[[], Any]] = None,
        market_calendar_service: Optional[Any] = None,
        broker: Optional[Any] = None,
        websocket_probe: Optional[Callable[[], Awaitable[dict]]] = None,
        api_budget_limiter: Optional[Any] = None,
        event_shadow_dir: str = "logs/strategies/event_shadow",
        event_shadow_max_age_days: int = 3,
        expected_config_hash: str | None = None,
        time_provider: Callable[[], float] = time.time,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._config_loader = config_loader
        self._env_provider = env_provider
        self._mcs = market_calendar_service
        self._broker = broker
        self._websocket_probe = websocket_probe
        self._api_budget_limiter = api_budget_limiter
        self._event_shadow_dir = event_shadow_dir
        self._event_shadow_max_age_days = event_shadow_max_age_days
        self._expected_config_hash = expected_config_hash
        self._time_provider = time_provider
        self._now_provider = now_provider
        self._cached_config: Any = None

    # -- helpers ------------------------------------------------------------

    async def _measured(self, name: str, fn: Callable[[], Awaitable[CheckResult]]) -> CheckResult:
        t0 = self._time_provider()
        try:
            result = await fn()
        except Exception as exc:  # noqa: BLE001 — top-level boundary
            result = CheckResult(name=name, status=CheckStatus.FAIL, detail=f"예외 발생: {exc!r}")
        result.elapsed_ms = int((self._time_provider() - t0) * 1000)
        result.name = name
        return result

    # -- individual checks --------------------------------------------------

    async def check_config(self) -> CheckResult:
        async def run() -> CheckResult:
            cfg = self._config_loader()
            self._cached_config = cfg
            mode = "paper" if getattr(cfg, "is_paper_trading", True) else "real"
            current_hash = compute_config_hash(cfg)
            if self._expected_config_hash and current_hash != self._expected_config_hash:
                return CheckResult(
                    name="",
                    status=CheckStatus.WARN,
                    detail=(
                        f"config_hash diff: expected={self._expected_config_hash} "
                        f"current={current_hash or '<empty>'} (is_paper_trading={mode})"
                    ),
                )
            return CheckResult(
                name="",
                status=CheckStatus.PASS,
                detail=f"config 로드 성공 (is_paper_trading={mode}, config_hash={current_hash or '<empty>'})",
            )

        return await self._measured("config_validation", run)

    async def check_token_env_consistency(self) -> CheckResult:
        async def run() -> CheckResult:
            cfg = self._cached_config or self._config_loader()
            self._cached_config = cfg

            is_paper = bool(getattr(cfg, "is_paper_trading", True))
            account = getattr(cfg, "stock_account_number", None)
            api_key = getattr(cfg, "api_key", None) if not is_paper else getattr(cfg, "paper_api_key", None)
            base_url = getattr(cfg, "url", None) if not is_paper else getattr(cfg, "paper_url", None)
            ws_url = (
                getattr(cfg, "websocket_url", None)
                if not is_paper
                else getattr(cfg, "paper_websocket_url", None)
            )

            errors: List[str] = []
            if not account:
                errors.append("stock_account_number 누락")
            if not api_key:
                errors.append("api_key 누락 (mode=%s)" % ("paper" if is_paper else "real"))
            if not base_url:
                errors.append("base_url 누락")
            else:
                if is_paper and not _looks_like_paper(base_url):
                    errors.append(f"is_paper_trading=true 인데 base_url 이 paper 호스트가 아님: {base_url}")
                if not is_paper and not _looks_like_real(base_url):
                    errors.append(f"is_paper_trading=false 인데 base_url 이 real 호스트가 아님: {base_url}")
            if ws_url:
                if is_paper and not _looks_like_paper(ws_url):
                    errors.append(f"websocket_url paper 호스트 불일치: {ws_url}")
                if not is_paper and not _looks_like_real(ws_url):
                    errors.append(f"websocket_url real 호스트 불일치: {ws_url}")

            if errors:
                return CheckResult(name="", status=CheckStatus.FAIL, detail="; ".join(errors))
            return CheckResult(
                name="",
                status=CheckStatus.PASS,
                detail=f"mode={'paper' if is_paper else 'real'}, base_url ok, ws ok",
            )

        return await self._measured("broker_env_consistency", run)

    async def check_latest_trading_date(self) -> CheckResult:
        async def run() -> CheckResult:
            if self._mcs is None:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="MarketCalendarService 미주입")

            latest = await self._mcs.get_latest_trading_date()
            if not latest:
                return CheckResult(name="", status=CheckStatus.FAIL, detail="get_latest_trading_date() 가 None 또는 빈 값을 반환")

            now = self._now_provider()
            today_str = now.strftime("%Y%m%d")
            if latest == today_str:
                return CheckResult(name="", status=CheckStatus.PASS, detail=f"latest={latest} (오늘)")

            # 7일 이내면 휴장일/연휴로 간주 — PASS, 그 이상이면 WARN
            try:
                latest_dt = datetime.strptime(latest, "%Y%m%d")
            except ValueError:
                return CheckResult(name="", status=CheckStatus.FAIL, detail=f"latest 포맷 비정상: {latest!r}")
            delta = (now - latest_dt).days
            if delta <= 7:
                return CheckResult(name="", status=CheckStatus.PASS, detail=f"latest={latest} ({delta}일 전)")
            return CheckResult(name="", status=CheckStatus.WARN, detail=f"latest={latest} ({delta}일 전 — 연휴/장기 휴장 확인 필요)")

        return await self._measured("latest_trading_date", run)

    async def check_event_shadow(self) -> CheckResult:
        async def run() -> CheckResult:
            shadow_dir = Path(self._event_shadow_dir)
            if not shadow_dir.exists():
                return CheckResult(
                    name="",
                    status=CheckStatus.WARN,
                    detail=f"디렉터리 없음: {shadow_dir} (shadow 모드 미운영이면 정상)",
                )

            files = sorted(shadow_dir.glob("*.jsonl"))
            if not files:
                return CheckResult(
                    name="",
                    status=CheckStatus.WARN,
                    detail=f"jsonl 로그 없음: {shadow_dir}",
                )

            latest = files[-1]
            try:
                mtime = datetime.fromtimestamp(latest.stat().st_mtime)
            except OSError as exc:
                return CheckResult(name="", status=CheckStatus.FAIL, detail=f"stat 실패: {exc!r}")

            now = self._now_provider()
            age_days = (now - mtime).days
            if age_days > self._event_shadow_max_age_days:
                return CheckResult(
                    name="",
                    status=CheckStatus.FAIL,
                    detail=f"최신 shadow 로그가 {age_days}일 전 (한도: {self._event_shadow_max_age_days}일): {latest.name}",
                )
            return CheckResult(
                name="",
                status=CheckStatus.PASS,
                detail=f"최신: {latest.name} ({age_days}일 전)",
            )

        return await self._measured("event_shadow_status", run)

    async def check_websocket_subscription(self, *, offline: bool = False) -> CheckResult:
        async def run() -> CheckResult:
            if offline:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="offline 모드 — live WebSocket 점검 생략")
            if self._websocket_probe is None:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="websocket_probe 미주입")

            probe_result = await self._websocket_probe()
            if not probe_result:
                return CheckResult(name="", status=CheckStatus.FAIL, detail="probe 응답 없음")
            connected = bool(probe_result.get("connected"))
            last_tick_age_sec = probe_result.get("last_tick_age_sec")
            subscriptions = probe_result.get("subscriptions", 0)

            if not connected:
                return CheckResult(
                    name="",
                    status=CheckStatus.FAIL,
                    detail=f"WebSocket 미연결 (subscriptions={subscriptions})",
                )
            if last_tick_age_sec is None:
                return CheckResult(
                    name="",
                    status=CheckStatus.WARN,
                    detail=f"수신 시각 정보 없음 (subscriptions={subscriptions})",
                )
            if last_tick_age_sec > 5.0:
                return CheckResult(
                    name="",
                    status=CheckStatus.FAIL,
                    detail=f"마지막 수신 {last_tick_age_sec:.1f}s 전 (한도: 5s, subscriptions={subscriptions})",
                )
            return CheckResult(
                name="",
                status=CheckStatus.PASS,
                detail=f"connected, last_tick={last_tick_age_sec:.1f}s, subscriptions={subscriptions}",
            )

        return await self._measured("websocket_subscription_health", run)

    async def check_account_snapshot(self, *, offline: bool = False) -> CheckResult:
        async def run() -> CheckResult:
            if offline:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="offline 모드 — broker 계좌 조회 생략")
            if self._broker is None:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="broker 미주입")

            t0 = self._time_provider()
            resp = await self._broker.get_account_balance()
            elapsed = self._time_provider() - t0

            rt_cd = getattr(resp, "rt_cd", None)
            if rt_cd != "0":
                return CheckResult(
                    name="",
                    status=CheckStatus.FAIL,
                    detail=f"get_account_balance rt_cd={rt_cd!r} (msg={getattr(resp, 'msg1', '')})",
                )
            if elapsed > 30.0:
                return CheckResult(
                    name="",
                    status=CheckStatus.WARN,
                    detail=f"응답 {elapsed:.1f}s (한도: 30s)",
                )
            return CheckResult(
                name="",
                status=CheckStatus.PASS,
                detail=f"rt_cd=0, elapsed={elapsed:.2f}s",
            )

        return await self._measured("account_snapshot_freshness", run)

    async def check_api_budget_limiter(self) -> CheckResult:
        async def run() -> CheckResult:
            if self._api_budget_limiter is None:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="ApiBudgetLimiter 미주입")
            if not hasattr(self._api_budget_limiter, "snapshot"):
                return CheckResult(name="", status=CheckStatus.FAIL, detail="snapshot() 미지원 limiter")

            snapshot = self._api_budget_limiter.snapshot()
            if not isinstance(snapshot, dict):
                return CheckResult(name="", status=CheckStatus.FAIL, detail="snapshot() 결과가 dict 아님")

            required = {
                "quotation_price",
                "quotation_ohlcv",
                "account_balance",
                "account_reconciliation",
                "order_submit",
                "order_cancel",
                "websocket_connect",
                "websocket_subscribe",
            }
            missing = sorted(required - set(snapshot.keys()))
            if missing:
                return CheckResult(
                    name="",
                    status=CheckStatus.WARN,
                    detail=f"missing categories: {', '.join(missing)}",
                )

            summary = []
            for category in sorted(required):
                data = snapshot.get(category) or {}
                summary.append(
                    f"{category}(limit={data.get('limit')}, "
                    f"rate={data.get('rate_limit_per_sec')}, active={data.get('active')})"
                )
            return CheckResult(
                name="",
                status=CheckStatus.PASS,
                detail="; ".join(summary),
            )

        return await self._measured("api_budget_limiter", run)

    async def check_real_mode_policy_strictness(self) -> CheckResult:
        """real 모드일 때 effective PositionSizing / RiskGate / OrderPolicy 값이
        canary 임계보다 느슨하면 WARN 또는 FAIL 을 낸다.

        canary 임계는 각 RealOverrides 클래스의 default 값 (P0 0-2 정책 합의값).
        loose factor 1.5× 까지는 WARN, 그 이상은 FAIL.
        OrderPolicy.allow_market_buy=True 는 real 모드에서 즉시 FAIL.
        paper 모드면 SKIPPED.
        """
        async def run() -> CheckResult:
            cfg = self._cached_config or self._config_loader()
            self._cached_config = cfg
            is_paper = bool(getattr(cfg, "is_paper_trading", True))
            if is_paper:
                return CheckResult(name="", status=CheckStatus.SKIPPED, detail="paper 모드 — strictness check skip")

            warns: List[str] = []
            fails: List[str] = []

            ps_cfg = getattr(cfg, "position_sizing", None)
            rg_cfg = getattr(cfg, "risk_gate", None)
            op_cfg = getattr(cfg, "order_policy", None)

            def _grade(name: str, effective: float, canary: float, *, lower_is_safer: bool = True) -> None:
                """effective 가 canary 임계보다 얼마나 느슨한지 평가."""
                if lower_is_safer:
                    if effective > canary * 1.5:
                        fails.append(f"{name}={effective} (canary={canary}, loose>1.5x)")
                    elif effective > canary:
                        warns.append(f"{name}={effective} (canary={canary})")
                else:
                    if effective < canary / 1.5:
                        fails.append(f"{name}={effective} (canary={canary}, tight<0.67x)")
                    elif effective < canary:
                        warns.append(f"{name}={effective} (canary={canary})")

            if ps_cfg is not None:
                ov = ps_cfg.real_mode_overrides
                _grade("position_sizing.per_trade_risk_pct", ov.per_trade_risk_pct, 0.5)
                _grade("position_sizing.max_per_position_pct", ov.max_per_position_pct, 3.0)

            if rg_cfg is not None:
                ov = rg_cfg.real_mode_overrides
                _grade("risk_gate.max_total_exposure_pct", ov.max_total_exposure_pct, 30.0)
                _grade("risk_gate.max_pending_orders", ov.max_pending_orders, 5)

            if op_cfg is not None:
                ov = op_cfg.real_mode_overrides
                if ov.allow_market_buy:
                    fails.append("order_policy.allow_market_buy=True (real 모드 fail-close 위반)")
                _grade("order_policy.max_market_slippage_pct", ov.max_market_slippage_pct, 0.5)
                _grade("order_policy.max_spread_pct", ov.max_spread_pct, 0.5)
                _grade("order_policy.max_top_of_book_participation_pct", ov.max_top_of_book_participation_pct, 10.0)

            if fails:
                detail = "FAIL: " + "; ".join(fails)
                if warns:
                    detail += " | WARN: " + "; ".join(warns)
                return CheckResult(name="", status=CheckStatus.FAIL, detail=detail)
            if warns:
                return CheckResult(name="", status=CheckStatus.WARN, detail="WARN: " + "; ".join(warns))
            return CheckResult(name="", status=CheckStatus.PASS, detail="real 모드 정책이 canary 임계 안에 있음")

        return await self._measured("real_mode_policy_strictness", run)

    # -- runner -------------------------------------------------------------

    async def run_all(self, *, offline: bool = False) -> PreDeployCheckSummary:
        results: List[CheckResult] = []
        results.append(await self.check_config())
        results.append(await self.check_token_env_consistency())
        results.append(await self.check_latest_trading_date())
        results.append(await self.check_event_shadow())
        results.append(await self.check_websocket_subscription(offline=offline))
        results.append(await self.check_account_snapshot(offline=offline))
        results.append(await self.check_api_budget_limiter())
        results.append(await self.check_real_mode_policy_strictness())
        return PreDeployCheckSummary(results=results)
