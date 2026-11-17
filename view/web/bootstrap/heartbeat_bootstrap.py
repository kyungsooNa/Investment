"""외부 생존 감시(heartbeat ping) 태스크 조립. `heartbeat.url` 이 없으면 비활성."""
from __future__ import annotations

from task.background.always_on.heartbeat_ping_task import HeartbeatPingTask


def build_heartbeat_task(ctx):
    config = getattr(ctx, "full_config", None)
    if hasattr(config, "model_dump"):
        config = config.model_dump()
    cfg = (config or {}).get("heartbeat") or {}
    url = str(cfg.get("url") or "").strip()
    ctx.heartbeat_task = HeartbeatPingTask(
        url=url,
        interval_sec=float(cfg.get("interval_sec") or 60),
        logger=ctx.logger,
    ) if url else None
    return ctx.heartbeat_task
