"""
시스템 상태 및 캐시 모니터링 API 엔드포인트.
"""
import asyncio
import time
from fastapi import APIRouter, HTTPException, Query
from repositories.streaming_stock_repo import StreamingType
from view.web.api_common import _get_ctx
import view.web.api_common as api_common
from config.task_config_loader import load_after_market_delays

router = APIRouter()

# 태스크별 실행 스케줄 유형
# intraday: 장 중에만 의미있는 태스크 (장 마감 후 비활성)
# pre_market: 장 시작 전 준비/상태 점검 태스크
# after_market: 장 마감 후 실행되는 배치 태스크 (장 중 비활성)
# always_on: 항상 동작해야 하는 실시간 태스크
_SCHEDULE_TYPES = {
    "pre_market_health_check": "pre_market",
    "websocket_watchdog":  "intraday",
    "cache_warmup":       "pre_market",
    "strategy_scheduler":  "intraday",
    "ranking_refresh":     "after_market",
    "daily_price_collector": "after_market",
    "minervini_update":     "after_market",
    "ohlcv_update":        "after_market",
    "전일기준주도주_생성":  "after_market",
    "newhigh":             "after_market",
    "notification_queue_task":  "always_on",
    "program_trading_monitor":  "always_on",
}

_SCHEDULE_ORDER = {
    "always_on": 0,
    "pre_market": 1,
    "intraday": 2,
    "after_market": 3,
    "unknown": 99,
}


@router.get("/cache/status")
async def get_cache_status(expand: bool = True):
    """메모리 캐시 상태 및 적중률 통계 반환"""
    ctx = _get_ctx()
    latest_trading_date = await ctx._mcs.get_latest_trading_date() if ctx._mcs else None
    # get_cache_stats()는 CPU 집약적 작업(대량 JSON 직렬화)이므로 스레드 풀에서 실행해 이벤트 루프 블로킹을 방지
    stats = await asyncio.to_thread(
        ctx.get_cache_stats, expand=expand, latest_trading_date=latest_trading_date
    ) or {}

    if "items" not in stats:
        stats["items"] = []

    if stats.get("items"):
        for item in stats["items"]:
            code = item.get("code")
            if code:
                code_str = str(code).zfill(6)  # 확실한 6자리 문자열로 변환
                name = ctx.stock_code_repository.get_name_by_code(code_str)
                item["name"] = name if name and name != code_str else code_str

    return {
        "success": True,
        "data": stats
    }


@router.get("/debug/requests")
def get_active_requests():
    """현재 서버에서 처리 중인 in-flight 요청 목록 반환 (hang 진단용).

    ForegroundScheduler가 백그라운드 태스크 중단을 기다리거나,
    무거운 응답 직렬화로 이벤트 루프가 블로킹될 때 어떤 요청이 대기 중인지 확인한다.
    이 엔드포인트 자체는 Foreground 미들웨어를 우회하므로 hang 상태에서도 응답한다.
    """
    try:
        ctx = _get_ctx()
        fg = getattr(ctx, "foreground_scheduler", None)
    except Exception:
        fg = None

    now = time.monotonic()
    active = [
        {
            "path": r["path"],
            "method": r["method"],
            "elapsed_sec": round(now - r["start"], 1),
            "query": r["query"],
        }
        for r in api_common._active_requests.values()
    ]
    active.sort(key=lambda x: x["elapsed_sec"], reverse=True)
    return {
        "success": True,
        "count": len(active),
        "foreground": {
            "active_count": fg.active_count if fg else 0,
            "is_blocking_background": fg.is_active if fg else False,
        },
        "data": active,
    }


@router.get("/system/operations/status")
def get_operations_status():
    """운영 요약 상태 반환: 전략, 주문, 손익, 데이터 품질, WebSocket, 알림 큐."""
    ctx = _get_ctx()

    scheduler_status = {}
    scheduler = getattr(ctx, "scheduler", None)
    if scheduler is not None:
        try:
            raw = scheduler.get_status()
            scheduler_status = raw if isinstance(raw, dict) else {}
        except Exception:
            scheduler_status = {}

    strategies = scheduler_status.get("strategies") or []
    active_strategy_count = sum(1 for item in strategies if item.get("enabled"))
    position_count = sum(int(item.get("current_holds") or 0) for item in strategies)

    order_summary = {
        "active_order_count": 0,
        "unfilled_order_count": 0,
        "orders": [],
        "reconcile_alarm": False,
        "reconcile_mismatch_count": 0,
    }
    oes = getattr(ctx, "order_execution_service", None)
    if oes is not None and hasattr(oes, "get_active_order_summary"):
        try:
            summary = oes.get_active_order_summary()
            if isinstance(summary, dict):
                order_summary.update(summary)
        except Exception:
            pass

    pnl = _build_operations_pnl(ctx)

    dq = getattr(ctx, "data_quality_service", None)
    data_quality = dq.get_health() if dq is not None and hasattr(dq, "get_health") else {
        "enabled": False,
        "last_result": None,
    }

    broker = getattr(ctx, "broker", None)
    websocket = {
        "receive_alive": bool(broker and broker.is_websocket_receive_alive()),
    }
    watchdog = getattr(ctx, "websocket_watchdog_task", None)
    if watchdog is not None and hasattr(watchdog, "get_progress"):
        try:
            progress = watchdog.get_progress()
            if isinstance(progress, dict):
                websocket.update(progress)
        except Exception:
            pass

    ns = getattr(ctx, "notification_service", None)
    queue_depth = 0
    if ns is not None and hasattr(ns, "external_handler_queue"):
        try:
            queue_depth = ns.external_handler_queue.qsize()
        except Exception:
            queue_depth = 0

    kill_switch = None
    ks = getattr(ctx, "kill_switch_service", None)
    if ks is not None and hasattr(ks, "get_status"):
        try:
            kill_switch = ks.get_status()
        except Exception:
            kill_switch = None

    reconcile = None
    reconcile_task = getattr(ctx, "after_market_reconcile_task", None)
    if reconcile_task is not None and hasattr(reconcile_task, "get_progress"):
        try:
            reconcile = reconcile_task.get_progress()
        except Exception:
            reconcile = None

    sqs = getattr(ctx, "stock_query_service", None)
    _raw_stats = getattr(sqs, "_price_lookup_stats", None) if sqs else None
    price_lookup = dict(_raw_stats) if isinstance(_raw_stats, dict) else None
    api_budget = None
    limiter = getattr(ctx, "api_budget_limiter", None)
    if limiter is not None and hasattr(limiter, "snapshot"):
        try:
            snapshot = limiter.snapshot()
            api_budget = snapshot if isinstance(snapshot, dict) else None
        except Exception:
            api_budget = None

    return {
        "success": True,
        "data": {
            "active_strategy_count": active_strategy_count,
            "position_count": position_count,
            "orders": order_summary,
            "pnl": pnl,
            "data_quality": data_quality,
            "websocket": websocket,
            "notification_queue_depth": queue_depth,
            "kill_switch": kill_switch,
            "after_market_reconcile": reconcile,
            "price_lookup": price_lookup,
            "api_budget": api_budget,
        },
    }


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _build_operations_pnl(ctx) -> dict:
    realized = {
        "summary": None,
        "realized_pnl_won": None,
        "sold_count": 0,
    }
    evaluation = {
        "broker_total_equity": None,
        "available_cash": None,
        "position_eval_amount": None,
        "broker_position_count": 0,
        "virtual_holding_buy_amount": None,
        "estimated_unrealized_pnl_won": None,
        "snapshot_fetched_at": None,
    }
    day = {
        "current_return_pct": None,
        "daily_change_pct": None,
        "baseline_date": None,
    }

    vts = getattr(ctx, "virtual_trade_service", None)
    if vts is not None:
        try:
            realized["summary"] = vts.get_summary(apply_cost=True)
        except TypeError:
            try:
                realized["summary"] = vts.get_summary()
            except Exception:
                realized["summary"] = None
        except Exception:
            realized["summary"] = None

        try:
            trades = vts.get_all_trades(apply_cost=False)
            sold = [row for row in trades if row.get("status") == "SOLD"]
            realized["sold_count"] = len(sold)
            realized["realized_pnl_won"] = round(sum(
                (_to_float(row.get("sell_price")) - _to_float(row.get("buy_price")))
                * _to_float(row.get("qty"), 1.0)
                for row in sold
            ))
        except Exception:
            pass

        try:
            holds = vts.get_holds()
            evaluation["virtual_holding_buy_amount"] = round(sum(
                _to_float(row.get("buy_price")) * _to_float(row.get("qty"), 1.0)
                for row in holds
            ))
        except Exception:
            pass

        try:
            data = vts._load_data()
            daily = data.get("daily", {}) if isinstance(data, dict) else {}
            if daily:
                latest_date = sorted(daily.keys())[-1]
                current_return = daily.get(latest_date, {}).get("ALL")
                if current_return is not None:
                    day["current_return_pct"] = _to_float(current_return)
                    change, baseline_date = vts.get_daily_change("ALL", day["current_return_pct"], _data=data)
                    day["daily_change_pct"] = change
                    day["baseline_date"] = baseline_date
        except Exception:
            pass

    snapshot_cache = getattr(ctx, "account_snapshot_cache", None)
    snapshot = getattr(snapshot_cache, "_snapshot", None) if snapshot_cache is not None else None
    if snapshot is not None:
        try:
            positions = getattr(snapshot, "positions", {}) or {}
            position_eval_amount = sum(int(v or 0) for v in positions.values())
            evaluation.update({
                "broker_total_equity": getattr(snapshot, "total_equity", None),
                "available_cash": getattr(snapshot, "available_cash", None),
                "position_eval_amount": position_eval_amount,
                "broker_position_count": len(positions),
                "snapshot_fetched_at": getattr(snapshot, "fetched_at", None).isoformat()
                    if getattr(snapshot, "fetched_at", None) else None,
            })
            if evaluation["virtual_holding_buy_amount"] is not None:
                evaluation["estimated_unrealized_pnl_won"] = (
                    position_eval_amount - evaluation["virtual_holding_buy_amount"]
                )
        except Exception:
            pass

    return {
        "realized": realized,
        "evaluation": evaluation,
        "day": day,
    }


@router.get("/system/reconcile/history")
def get_reconcile_history(count: int = Query(20, ge=1, le=100)):
    """장 종료 후 주문/브로커 reconcile 결과 이력 반환."""
    ctx = _get_ctx()
    task = getattr(ctx, "after_market_reconcile_task", None)
    if task is not None and hasattr(task, "get_history"):
        try:
            return {"success": True, "data": task.get_history(count=count)}
        except Exception:
            pass
    return {"success": True, "data": []}


@router.get("/system/data-quality/history")
def get_data_quality_history(
    count: int = Query(50, ge=1, le=200),
    code: str | None = None,
    reason: str | None = None,
):
    """데이터 품질 위반/차단 이력 반환."""
    ctx = _get_ctx()
    dq = getattr(ctx, "data_quality_service", None)
    if dq is not None and hasattr(dq, "get_violation_history"):
        try:
            return {
                "success": True,
                "data": dq.get_violation_history(count=count, code=code, reason=reason),
            }
        except Exception:
            pass
    return {"success": True, "data": []}


# 1. 동기(def) 함수를 비동기(async def) 함수로 변경하여 이벤트 루프 데드락 방지
@router.get("/background/status")
async def get_background_status(): 
    """백그라운드 태스크 상태 및 진행률 반환"""
    ctx = _get_ctx()

    fg = getattr(ctx, "foreground_scheduler", None)
    foreground_info = {
        "active_count": fg.active_count if fg else 0,
        "is_blocking_background": fg.is_active if fg else False,
    }

    # TimeDispatcher 티켓 발행 현황
    latest_trading_date = await ctx._mcs.get_latest_trading_date() if ctx._mcs else None
    td = getattr(ctx, "time_dispatcher", None)
    time_dispatcher_info = None
    if td is not None:
        try:
            td_status = td.get_status()
            if isinstance(td_status, dict):
                last_date = td_status.get("last_dispatched_date")
                ticket_issued = isinstance(last_date, str) and last_date == latest_trading_date
                time_dispatcher_info = {
                    "last_dispatched_date": last_date,
                    "last_dispatched_at": td_status.get("last_dispatched_at"),
                    "market_is_open": td_status.get("market_is_open"),
                    "latest_trading_date": latest_trading_date,
                    "ticket_issued_today": ticket_issued,
                    "registered_tasks": td_status.get("registered_tasks", []),
                }
        except Exception:
            pass

    if not ctx.background_scheduler:
        result = []
        _append_program_trading_monitor_status(ctx, result)
        return {"success": True, "foreground": foreground_info, "time_dispatcher": time_dispatcher_info, "data": result}

    delays = load_after_market_delays()  # {task_name: delay_sec}

    result = []
    for item in ctx.background_scheduler.get_all_status():
        name = item["name"]
        task = ctx.background_scheduler.get_task(name)
        
        schedule_type = "unknown"
        if task:
            module_name = task.__class__.__module__
            configured_type = _SCHEDULE_TYPES.get(name)
            if configured_type:
                schedule_type = configured_type
            elif "strategy_scheduler" in module_name:
                schedule_type = "intraday"
            elif "after_market" in module_name:
                schedule_type = "after_market"
            elif "intraday" in module_name:
                schedule_type = "intraday"
            elif "always_on" in module_name:
                schedule_type = "always_on"

        # 2. Enum 객체 안전성 보장 (명시적 문자열 변환)
        raw_state = item.get("state")
        state_str = raw_state.value if hasattr(raw_state, "value") else str(raw_state)

        # 3. IDLE 상태일 경우 방어 로직: 대부분 태스크는 get_progress() 호출을 생략하지만,
        #    강제 수집 API로 직접 실행되는 태스크(예: force_run 즉시 실행)가 내부 플래그
        #    를 통해 진행 중임을 알릴 수 있으므로 그런 경우에는 get_progress()를 호출하여
        #    실제 진행 상태를 반영하도록 합니다.
        progress = None
        if task:
            if state_str == "idle" and name != "strategy_scheduler":
                # If the task exposes an internal progress or running flag, call get_progress()
                if (
                    name == "pre_market_health_check"
                    or getattr(task, "_is_refreshing", False)
                    or hasattr(task, "_progress")
                ):
                    try:
                        progress = task.get_progress()
                    except Exception as e:
                        progress = {"running": False, "error": str(e)}
                else:
                    # Default safe placeholder for not-yet-started tasks
                    progress = {"running": False, "status": "Waiting to start"}
            else:
                try:
                    progress = task.get_progress()
                except Exception as e:
                    progress = {"running": False, "error": str(e)}

        result.append({
            "name": name,
            "state": state_str,
            "priority": item.get("priority"),
            "schedule_type": schedule_type,
            "schedule_order": _SCHEDULE_ORDER.get(schedule_type, 99),
            "delay_sec": delays.get(name, 0),
            "progress": progress,
        })

    _append_program_trading_monitor_status(ctx, result)

    # 스케줄 유형(실시간 -> 장중 -> 장마감) 순서로 정렬, 같은 유형 내에서는 실행 순서(delay_sec) 기준
    result.sort(key=lambda x: (x["schedule_order"], x["delay_sec"]))
    return {"success": True, "foreground": foreground_info, "time_dispatcher": time_dispatcher_info, "data": result}


def _append_program_trading_monitor_status(ctx, result: list) -> None:
    """ProgramTradingStreamService 내부 루프를 background/status에 노출한다.

    이 루프는 SchedulableTask가 아니라 WebSocketWatchdogTask.start()에서 시작되므로
    BackgroundScheduler.get_all_status()에는 나타나지 않는다.
    """
    if any(item.get("name") == "program_trading_monitor" for item in result):
        return
    svc = getattr(ctx, "program_trading_stream_service", None)
    status_getter = getattr(type(svc), "get_background_task_status", None)
    if svc is None or not callable(status_getter):
        return
    try:
        progress = svc.get_background_task_status()
    except Exception as e:
        progress = {"running": False, "error": str(e)}
    running = bool(isinstance(progress, dict) and progress.get("running"))
    schedule_type = _SCHEDULE_TYPES["program_trading_monitor"]
    result.append({
        "name": "program_trading_monitor",
        "state": "running" if running else "idle",
        "priority": 50,
        "schedule_type": schedule_type,
        "schedule_order": _SCHEDULE_ORDER.get(schedule_type, 99),
        "delay_sec": 0,
        "progress": progress,
    })


# ── 장 마감 후 태스크 강제 수집 엔드포인트 ─────────────────────────────

@router.post("/background/ranking/force-update")
async def force_ranking_update():
    """skip 조건을 무시하고 투자자 랭킹을 강제 재수집한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "ranking_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="RankingTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "투자자 랭킹 강제 수집이 시작되었습니다."}


@router.post("/background/daily-price/force-update")
async def force_daily_price_update():
    """skip 조건을 무시하고 전 종목 현재가를 강제 재수집한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "daily_price_collector_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="DailyPriceCollectorTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_run(force_fresh=True))
    return {"success": True, "message": "현재가 강제 수집이 시작되었습니다."}


@router.get("/subscriptions/status")
def get_subscription_status():
    """실시간 현재가 구독 현황 반환 (우선순위별 종목 + 마지막 수신 시각)"""
    ctx = _get_ctx()
    svc = getattr(ctx, "price_subscription_service", None)
    if not svc:
        return {"success": True, "data": None}

    status = svc.get_status()

    def _code_set(value):
        if not isinstance(value, (set, frozenset, list, tuple)):
            return None
        return {str(code) for code in value if code is not None and str(code)}

    pending_by_priority = {
        "CRITICAL": set(status.get("pending_by_priority", {}).get("CRITICAL", [])),
        "HIGH": set(status.get("pending_by_priority", {}).get("HIGH", [])),
        "MEDIUM": set(status.get("pending_by_priority", {}).get("MEDIUM", [])),
        "LOW": set(status.get("pending_by_priority", {}).get("LOW", [])),
    }

    streaming_svc = getattr(ctx, "streaming_service", None)
    active_codes_price = set(status.get("active_codes_price", []))
    active_codes_pt = set(status.get("active_codes_pt", []))

    repo = getattr(ctx, "streaming_stock_repo", None)
    if repo is not None:
        try:
            desired_pt = repo.get_desired(StreamingType.PROGRAM_TRADING)
            desired_pt_set = _code_set(desired_pt)
            if desired_pt_set is not None:
                pending_by_priority["CRITICAL"] = desired_pt_set

            active_pt_set = _code_set(repo.get_active(StreamingType.PROGRAM_TRADING))
            if active_pt_set is not None:
                active_codes_pt.update(active_pt_set)

            active_price_set = _code_set(repo.get_active(StreamingType.UNIFIED_PRICE))
            if active_price_set is not None:
                active_codes_price.update(active_price_set)
        except Exception:
            pass

    pending_count = len(set().union(*pending_by_priority.values()))
    active_set = active_codes_price | active_codes_pt
    active_count = len(active_codes_price) + len(active_codes_pt)

    def _enrich(codes: list) -> list:
        result = []
        for code in codes:
            name = ctx.stock_code_repository.get_name_by_code(code) or code
            price_info = streaming_svc.get_cached_realtime_price(code) if streaming_svc else None
            received_at = None
            price = None
            if isinstance(price_info, dict):
                received_at = price_info.get("received_at")
                price = price_info.get("price")
            result.append({
                "code": code,
                "name": name,
                "active": code in active_set, # 병합된 active_set을 통해 활성화 여부 확인
                "received_at": received_at,
                "price": price,
            })
        return result

    # API 반환 데이터 구성
    return {
        "success": True,
        "data": {
            "active_count": active_count,
            "max_subscriptions": status.get("max_subscriptions", 40),
            "active_codes_price": sorted(active_codes_price),
            "active_codes_pt": sorted(active_codes_pt),
            "pending_count": pending_count,
            "pending_by_priority": {
                "CRITICAL": _enrich(sorted(pending_by_priority["CRITICAL"])),
                "HIGH": _enrich(sorted(pending_by_priority["HIGH"])),
                "MEDIUM": _enrich(sorted(pending_by_priority["MEDIUM"])),
                "LOW": _enrich(sorted(pending_by_priority["LOW"])),
            }
        }
    }


def _unwrap_broker_client_layers(client):
    """Cache/Retry 래퍼를 벗겨 실제 KoreaInvestApiClient를 반환한다."""
    current = client
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        inner = getattr(current, "_client", None)
        if inner is None:
            break
        current = inner
    return current


@router.get("/subscriptions/debug")
def get_subscription_debug(codes: str | None = None):
    """실시간 구독 내부 상태를 종목별로 자세히 반환한다."""
    ctx = _get_ctx()
    sub_svc = getattr(ctx, "price_subscription_service", None)
    stream_svc = getattr(ctx, "streaming_service", None)
    price_svc = getattr(ctx, "price_stream_service", None)
    repo = getattr(ctx, "streaming_stock_repo", None)
    broker = getattr(ctx, "broker", None)

    if not (sub_svc and stream_svc and price_svc and repo and broker):
        return {"success": True, "data": None}

    requested_codes = []
    if codes:
        requested_codes = [c.strip() for c in codes.split(",") if c.strip()]

    status = sub_svc.get_status()
    active_price_policy = set(status.get("active_codes_price", []))
    active_pt_policy = set(status.get("active_codes_pt", []))
    desired_price = repo.get_desired(StreamingType.UNIFIED_PRICE)
    desired_pt = repo.get_desired(StreamingType.PROGRAM_TRADING)
    active_price_repo = repo.get_active(StreamingType.UNIFIED_PRICE)
    active_pt_repo = repo.get_active(StreamingType.PROGRAM_TRADING)

    raw_client = _unwrap_broker_client_layers(getattr(broker, "_client", None))
    websocket_api = getattr(raw_client, "_websocketAPI", None)
    subscribed_items = getattr(websocket_api, "_subscribed_items", set()) if websocket_api else set()
    pending_requests = getattr(websocket_api, "_pending_requests", {}) if websocket_api else {}

    target_codes = requested_codes
    if not target_codes:
        target_codes = sorted(
            desired_price
            | desired_pt
            | active_price_repo
            | active_pt_repo
            | active_price_policy
            | active_pt_policy
        )

    rows = []
    for code in target_codes:
        cached = stream_svc.get_cached_realtime_price(code)
        broker_items = sorted(
            tr_id for tr_id, tr_key in subscribed_items
            if tr_key == code
        )
        broker_pending = sorted(
            tr_id for (tr_id, tr_key) in pending_requests.keys()
            if tr_key == code
        )
        rows.append({
            "code": code,
            "name": ctx.stock_code_repository.get_name_by_code(code) or code,
            "desired_price": code in desired_price,
            "desired_pt": code in desired_pt,
            "repo_active_price": code in active_price_repo,
            "repo_active_pt": code in active_pt_repo,
            "policy_active_price": code in active_price_policy,
            "policy_active_pt": code in active_pt_policy,
            "is_subscribed_realtime_price": stream_svc.is_subscribed_realtime_price(code),
            "cached_price": cached.get("price") if isinstance(cached, dict) else None,
            "cached_received_at": cached.get("received_at") if isinstance(cached, dict) else None,
            "last_tick_ts": price_svc.get_last_tick_ts(code),
            "subscription_age_sec": price_svc.get_subscription_age(code),
            "broker_subscribed_tr_ids": broker_items,
            "broker_pending_tr_ids": broker_pending,
        })

    return {
        "success": True,
        "data": {
            "receive_alive": broker.is_websocket_receive_alive(),
            "rows": rows,
        }
    }


@router.post("/background/watchlist/force-update")
async def force_watchlist_update():
    """skip 조건을 무시하고 전일 기준 우량주를 강제 재생성한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "premium_watchlist_generator_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="PremiumWatchlistGeneratorTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 생성이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "전일기준우량주 강제 생성이 시작되었습니다."}


@router.post("/background/cache-warmup/force-update")
async def force_cache_warmup():
    """skip 조건을 무시하고 캐시 웜업을 강제 실행한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "cache_warmup_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="CacheWarmupTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 웜업이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "캐시 웜업이 시작되었습니다."}


@router.post("/background/newhigh/force-update")
async def force_newhigh_update():
    """skip 조건을 무시하고 52주 신고가 탐색을 강제 실행한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "newhigh_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="NewHighTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 탐색이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "52주 신고가 강제 탐색이 시작되었습니다."}


@router.post("/background/strategy-log-report/force-update")
async def force_strategy_log_report():
    """skip 조건을 무시하고 당일 전략 로그 리포트를 강제로 생성/전송한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "strategy_log_report_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="StrategyLogReportTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 리포트 생성이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "전략 로그 리포트 강제 생성이 시작되었습니다."}


# ── 자금 한도 설정 ──────────────────────────────────────────────────────

from pydantic import BaseModel as _BaseModel, Field as _Field
from typing import Optional as _Optional
from config.config_loader import RiskGateConfig as _RiskGateConfig, PositionSizingConfig as _PositionSizingConfig


class PositionSizingLimitsRequest(_BaseModel):
    max_order_amount_won: _Optional[int] = _Field(None, ge=0, description="종목당 단건 주문 한도 (원, 0=무제한)")
    max_per_position_pct: _Optional[float] = _Field(None, ge=0.0, le=100.0, description="단일 종목 비중 상한 (%)")


@router.get("/position-sizing/limits")
def get_position_sizing_limits():
    """현재 설정된 자금 한도를 반환한다."""
    ctx = _get_ctx()
    rg = getattr(ctx.full_config, "risk_gate", None)
    ps = getattr(ctx.full_config, "position_sizing", None)
    return {
        "max_order_amount_won": rg.max_order_amount_won if rg else None,
        "max_per_position_pct": ps.max_per_position_pct if ps else None,
        "defaults": {
            "max_order_amount_won": _RiskGateConfig().max_order_amount_won,
            "max_per_position_pct": _PositionSizingConfig().max_per_position_pct,
        },
    }


@router.post("/position-sizing/limits")
def update_position_sizing_limits(req: PositionSizingLimitsRequest):
    """자금 한도를 즉시 변경하고 state 파일에 저장한다."""
    ctx = _get_ctx()
    rg = getattr(ctx.full_config, "risk_gate", None)
    ps = getattr(ctx.full_config, "position_sizing", None)

    if req.max_order_amount_won is not None and rg is not None:
        rg.max_order_amount_won = req.max_order_amount_won
    if req.max_per_position_pct is not None and ps is not None:
        ps.max_per_position_pct = req.max_per_position_pct

    ctx.save_position_sizing_state()

    return {
        "success": True,
        "max_order_amount_won": rg.max_order_amount_won if rg else None,
        "max_per_position_pct": ps.max_per_position_pct if ps else None,
    }


@router.post("/background/minervini/force-update")
async def force_minervini_update():
    """skip 조건을 무시하고 Minervini Stage2 캐시를 강제 갱신한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "minervini_update_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="MinerviniUpdateTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "Minervini S2 강제 갱신이 시작되었습니다."}


@router.post("/background/reconcile/force-update")
async def force_after_market_reconcile():
    """주문/브로커 상태 reconcile을 즉시 강제 실행한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "after_market_reconcile_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="AfterMarketReconcileTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 주문/브로커 검증이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "주문/브로커 상태 강제 검증이 시작되었습니다."}
