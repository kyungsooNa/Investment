"""시장별(국내/미국) 스케줄러 제어의 교차 경로 가드 (S-1).

`test_it_scheduler_status_market_tasks.py` 는 **상태 조회**만 덮는다. 정작 위험한 것은
제어 경로다 — 미국장 버튼이 국내 스케줄러를 멈추면 실주문 전략이 조용히 죽는다.
분리(#904) 직후 배선 결함이 한 번 났고(#919), 그 위에 태스크 변경이 계속 쌓였다
(#932 태스크 통합·개명, #935 대사 태스크 2건 추가, #952/#956/#962/#965 상태 표기).

여기서 고정하는 계약은 셋이다.

1. **시장 인자는 대상 스케줄러를 고른다** — 한 시장에 건 제어가 다른 시장 인스턴스를
   건드리지 않는다.
2. **없는 시장은 폴백하지 않는다** — 미국장 스케줄러가 없을 때 국내 스케줄러로 흘러가
   "성공" 하면 안 된다. 503 으로 막히고 **어느 쪽도 건드리지 않아야** 한다.
   (`_get_strategy_scheduler` 의 `market == "domestic"` 폴백이 비도메스틱 시장까지
   번지는 것이 이 계열의 전형적 실패다.)
3. **상태 표기** — `running` 은 실행 순간에만 참이라 '가동 중 대기' 와 '미기동' 을
   구분하지 못한다. 그래서 태스크가 `armed` 를 알려주면 그 값을 쓰고, 알려주지 않을
   때만 `running` 으로 폴백한다(#962/#965). 같은 증상이 네 번 반복된 축이라 표기
   의미를 테스트로 박아둔다.
"""
from unittest.mock import MagicMock

import pytest

from interfaces.schedulable_task import TaskPriority, TaskState


class _FakeScheduler:
    """제어 호출을 기록만 하는 스케줄러. 어느 인스턴스가 불렸는지가 이 테스트의 관심사다."""

    def __init__(self, market: str, strategies=("larry_williams_vbo",)):
        self.market = market
        self.calls: list = []
        self._strategies = set(strategies)
        # 라우트가 상태 저장을 백그라운드로 던진다(`_save_scheduler_state_later`).
        self._save_scheduler_state = MagicMock()

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self, save_state: bool = True) -> None:
        self.calls.append(("stop", save_state))

    def clear_saved_state(self) -> None:
        self.calls.append("clear_saved_state")

    async def start_strategy(self, name: str) -> bool:
        self.calls.append(("start_strategy", name))
        return name in self._strategies

    async def stop_strategy(self, name: str) -> bool:
        self.calls.append(("stop_strategy", name))
        return name in self._strategies

    def get_status(self) -> dict:
        return {"running": False, "dry_run": False, "strategies": []}


class _FakeMarketTask:
    """`_MARKET_TASK_DEFINITIONS` 가 가리키는 미국장 태스크 대역."""

    priority = TaskPriority.NORMAL

    def __init__(self, name: str, state=TaskState.IDLE, progress=None):
        self.task_name = name
        self.state = state
        self._progress = progress or {}

    def get_progress(self) -> dict:
        return dict(self._progress)


@pytest.fixture
def two_market_ctx(mock_paper_ctx):
    """국내/미국 스케줄러가 모두 있는 컨텍스트."""
    domestic = _FakeScheduler("domestic")
    overseas = _FakeScheduler("overseas_us")
    mock_paper_ctx.scheduler = domestic
    mock_paper_ctx.strategy_schedulers = {
        "domestic": domestic,
        "overseas_us": overseas,
    }
    mock_paper_ctx.enabled_market_modes = ["domestic", "overseas_us"]
    mock_paper_ctx.background_scheduler = None
    return mock_paper_ctx, domestic, overseas


# ─────────────────────────────────────────────────────────────────────────────
# 계약 1 — 시장 인자가 대상 인스턴스를 고른다
# ─────────────────────────────────────────────────────────────────────────────


def test_it_start_targets_only_the_requested_market(paper_client, two_market_ctx):
    _, domestic, overseas = two_market_ctx

    response = paper_client.post("/api/scheduler/start?market=overseas_us")

    assert response.status_code == 200
    assert "start" in overseas.calls
    assert domestic.calls == [], (
        f"미국장 시작 요청이 국내 스케줄러를 건드렸습니다: {domestic.calls}"
    )


def test_it_stop_targets_only_the_requested_market(paper_client, two_market_ctx):
    _, domestic, overseas = two_market_ctx

    response = paper_client.post("/api/scheduler/stop?market=domestic")

    assert response.status_code == 200
    # 수동 정지는 저장된 자동복원 상태까지 지운다 — 재시작 시 되살아나면 안 되기 때문.
    assert ("stop", False) in domestic.calls
    assert "clear_saved_state" in domestic.calls
    assert overseas.calls == [], (
        f"국내 정지 요청이 미국장 스케줄러를 건드렸습니다: {overseas.calls}"
    )


def test_it_strategy_control_targets_only_the_requested_market(paper_client, two_market_ctx):
    """같은 전략 이름이 양쪽에 있어도 요청한 시장의 스케줄러만 움직인다."""
    _, domestic, overseas = two_market_ctx

    started = paper_client.post(
        "/api/scheduler/strategy/larry_williams_vbo/start?market=overseas_us"
    )
    assert started.status_code == 200
    assert ("start_strategy", "larry_williams_vbo") in overseas.calls
    assert domestic.calls == []

    overseas.calls.clear()
    stopped = paper_client.post(
        "/api/scheduler/strategy/larry_williams_vbo/stop?market=domestic"
    )
    assert stopped.status_code == 200
    assert ("stop_strategy", "larry_williams_vbo") in domestic.calls
    assert overseas.calls == []


def test_it_default_market_is_domestic(paper_client, two_market_ctx):
    """market 을 생략한 기존 호출은 종전대로 국내를 향한다(하위 호환)."""
    _, domestic, overseas = two_market_ctx

    response = paper_client.post("/api/scheduler/start")

    assert response.status_code == 200
    assert "start" in domestic.calls
    assert overseas.calls == []


# ─────────────────────────────────────────────────────────────────────────────
# 계약 2 — 없는 시장은 폴백하지 않는다
# ─────────────────────────────────────────────────────────────────────────────


def test_it_missing_overseas_scheduler_does_not_fall_back_to_domestic(
    paper_client, mock_paper_ctx
):
    """미국장 스케줄러가 없으면 503 이고, 국내 스케줄러는 건드리지 않는다."""
    domestic = _FakeScheduler("domestic")
    mock_paper_ctx.scheduler = domestic
    mock_paper_ctx.strategy_schedulers = {"domestic": domestic}
    mock_paper_ctx.enabled_market_modes = ["domestic", "overseas_us"]
    mock_paper_ctx.background_scheduler = None

    for path in (
        "/api/scheduler/start?market=overseas_us",
        "/api/scheduler/stop?market=overseas_us",
        "/api/scheduler/strategy/larry_williams_vbo/start?market=overseas_us",
    ):
        response = paper_client.post(path)
        assert response.status_code == 503, f"{path} 가 503 이 아닙니다: {response.status_code}"

    assert domestic.calls == [], (
        f"미국장 요청이 국내 스케줄러로 흘렀습니다: {domestic.calls}. "
        "비도메스틱 시장은 폴백 없이 막혀야 합니다."
    )


def test_it_unknown_market_touches_no_scheduler(paper_client, two_market_ctx):
    _, domestic, overseas = two_market_ctx

    response = paper_client.post("/api/scheduler/stop?market=overseas_jp")

    assert response.status_code == 503
    assert domestic.calls == []
    assert overseas.calls == []


# ─────────────────────────────────────────────────────────────────────────────
# 계약 3 — 상태 표기(running / armed)와 시장별 태스크 격리
# ─────────────────────────────────────────────────────────────────────────────


def test_it_market_tasks_do_not_leak_across_markets(paper_client, two_market_ctx):
    ctx, _, _ = two_market_ctx
    ctx.background_scheduler = MagicMock()
    ctx.background_scheduler.get_task.side_effect = lambda name: (
        _FakeMarketTask(name, progress={"running": True}) if name == "overseas_intraday" else None
    )

    domestic_status = paper_client.get("/api/scheduler/status?market=domestic").json()
    overseas_status = paper_client.get("/api/scheduler/status?market=overseas_us").json()

    assert domestic_status["market_tasks"] == [], (
        "미국장 태스크가 한국장 화면에 섞였습니다."
    )
    assert [item["name"] for item in overseas_status["market_tasks"]] == ["overseas_intraday"]


def test_it_armed_task_is_reported_as_armed_while_idle(paper_client, two_market_ctx):
    """폴링 루프는 패스 사이에 IDLE 로 돌아온다 — 그때 '미기동' 으로 보이면 안 된다."""
    ctx, _, _ = two_market_ctx
    ctx.background_scheduler = MagicMock()
    ctx.background_scheduler.get_task.side_effect = lambda name: (
        _FakeMarketTask(
            name,
            state=TaskState.IDLE,
            progress={"running": False, "armed": True, "phase": "waiting_next_pass"},
        )
        if name == "overseas_intraday"
        else None
    )

    body = paper_client.get("/api/scheduler/status?market=overseas_us").json()
    task = body["market_tasks"][0]

    assert task["running"] is False
    assert task["armed"] is True, "가동 중 대기가 '미기동' 으로 보고됐습니다."
    assert task["progress"]["phase"] == "waiting_next_pass"


def test_it_armed_falls_back_to_running_when_task_is_silent(paper_client, two_market_ctx):
    """`armed` 를 알려주지 않는 태스크는 종전대로 running 을 따른다(하위 호환)."""
    ctx, _, _ = two_market_ctx
    ctx.background_scheduler = MagicMock()
    ctx.background_scheduler.get_task.side_effect = lambda name: (
        _FakeMarketTask(name, state=TaskState.RUNNING, progress={})
        if name == "overseas_dryrun"
        else None
    )

    body = paper_client.get("/api/scheduler/status?market=overseas_us").json()
    task = body["market_tasks"][0]

    assert task["name"] == "overseas_dryrun"
    assert task["running"] is True
    assert task["armed"] is True


def test_it_stopped_task_is_neither_running_nor_armed(paper_client, two_market_ctx):
    ctx, _, _ = two_market_ctx
    ctx.background_scheduler = MagicMock()
    ctx.background_scheduler.get_task.side_effect = lambda name: (
        _FakeMarketTask(name, state=TaskState.STOPPED, progress={"running": False, "armed": False})
        if name == "overseas_intraday"
        else None
    )

    body = paper_client.get("/api/scheduler/status?market=overseas_us").json()
    task = body["market_tasks"][0]

    assert task["running"] is False
    assert task["armed"] is False
