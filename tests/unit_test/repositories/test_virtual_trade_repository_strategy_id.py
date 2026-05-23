"""VirtualTradeRepository — P3-4 Phase 2 PR 2a strategy_id compat layer.

Phase 2 의 첫 단계: runtime resolver 로 strategy_id ↔ display 변환을 흡수해
디스크에 남은 legacy 한국어 행을 in-memory 에서 strategy_id 로 정규화한다.

이 테스트는 다음 동작을 잠근다:
- 신규 write 는 항상 strategy_id 로 기록 (log_buy, log_order_failure, save_daily_snapshot)
- 모든 read 는 dual-key (id OR display) 로 legacy 행도 검색
- 미지 strategy (resolver 매핑에 없는 값) 은 그대로 read/write 통과 (passthrough)
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from repositories.virtual_trade_repository import VirtualTradeRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "vtr.db")
    return VirtualTradeRepository(db_path=db_path)


@pytest.fixture
def repo_weekday(tmp_path):
    """save_daily_snapshot 의 주말 제외 로직을 우회하기 위한 평일 고정 repo."""
    db_path = str(tmp_path / "vtr.db")
    clock = MagicMock()
    # 2026-05-22 (Friday) 09:00 KST
    fixed = datetime(2026, 5, 22, 9, 0, 0, tzinfo=pytz.timezone("Asia/Seoul"))
    clock.get_current_kst_time.return_value = fixed
    return VirtualTradeRepository(db_path=db_path, market_clock=clock)


# ---------- write 정규화 ----------

def test_log_buy_with_display_name_stores_strategy_id(repo):
    repo.log_buy("거래량돌파", "005930", 70000, qty=1)
    rows = repo._db.execute("SELECT strategy, code FROM trades WHERE code='005930'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "volume_breakout_live"


def test_log_buy_with_strategy_id_stores_strategy_id(repo):
    repo.log_buy("volume_breakout_live", "005930", 70000, qty=1)
    rows = repo._db.execute("SELECT strategy FROM trades WHERE code='005930'").fetchall()
    assert rows[0][0] == "volume_breakout_live"


def test_log_buy_passthrough_unknown_strategy_name(repo):
    repo.log_buy("test_only_xyz", "005930", 70000, qty=1)
    rows = repo._db.execute("SELECT strategy FROM trades WHERE code='005930'").fetchall()
    assert rows[0][0] == "test_only_xyz"


def test_log_order_failure_normalizes_strategy_name(repo):
    repo.log_order_failure("BUY", "005930", 70000, 1, "예약불가", strategy_name="거래량돌파")
    rows = repo._db.execute("SELECT strategy, status FROM trades WHERE code='005930'").fetchall()
    assert rows[0][0] == "volume_breakout_live"
    assert rows[0][1] == "FAILED"


def test_log_order_failure_without_strategy_keeps_action_label(repo):
    repo.log_order_failure("BUY", "005930", 70000, 1, "예약불가", strategy_name="")
    rows = repo._db.execute("SELECT strategy FROM trades WHERE code='005930'").fetchall()
    assert rows[0][0] == "BUY실패"


# ---------- read dual-key ----------

def _seed_legacy_korean_hold(repo, strategy_korean: str, code: str, price: int):
    """compat layer 우회: legacy 한국어 행을 직접 INSERT."""
    with repo._db:
        repo._db.execute(
            "INSERT INTO trades (strategy, code, buy_date, buy_price, qty, sell_date, sell_price, "
            "return_rate, status, reason, volatility_20d_annualized) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (strategy_korean, code, "2026-05-23 09:00:00", price, 1, None, None, 0.0, "HOLD", "", None),
        )


def test_is_holding_finds_legacy_korean_row_with_id_query(repo):
    _seed_legacy_korean_hold(repo, "거래량돌파", "005930", 70000)
    assert repo.is_holding("volume_breakout_live", "005930") is True


def test_is_holding_finds_strategy_id_row_with_korean_query(repo):
    repo.log_buy("volume_breakout_live", "005930", 70000)
    assert repo.is_holding("거래량돌파", "005930") is True


def test_is_holding_returns_false_when_no_match(repo):
    assert repo.is_holding("volume_breakout_live", "005930") is False


def test_get_holds_by_strategy_unions_legacy_and_new(repo):
    _seed_legacy_korean_hold(repo, "거래량돌파", "005930", 70000)
    repo.log_buy("volume_breakout_live", "000660", 130000)
    holds = repo.get_holds_by_strategy("volume_breakout_live")
    codes = sorted(h["code"] for h in holds)
    assert codes == ["000660", "005930"]


def test_log_sell_by_strategy_finds_legacy_korean_hold(repo):
    _seed_legacy_korean_hold(repo, "거래량돌파", "005930", 70000)
    return_rate = repo.log_sell_by_strategy("volume_breakout_live", "005930", 77000, qty=1)
    assert return_rate == 10.0
    row = repo._db.execute(
        "SELECT status, return_rate FROM trades WHERE code='005930'"
    ).fetchone()
    assert row[0] == "SOLD"


def test_log_sell_by_strategy_with_result_finds_legacy_korean(repo):
    _seed_legacy_korean_hold(repo, "거래량돌파", "005930", 70000)
    result = repo.log_sell_by_strategy_with_result(
        "volume_breakout_live", "005930", 77000, qty=1
    )
    assert result.return_rate == 10.0


# ---------- daily snapshot ----------

def test_save_daily_snapshot_normalizes_dict_keys(repo_weekday):
    repo_weekday.save_daily_snapshot({"거래량돌파": 0.05, "하이타이트플래그": 0.03, "ALL": 0.04})
    rows = repo_weekday._db.execute("SELECT strategy FROM snapshots").fetchall()
    stored = {r[0] for r in rows}
    assert "volume_breakout_live" in stored
    assert "high_tight_flag" in stored
    assert "ALL" in stored  # passthrough
    # legacy 한국어 키는 새 write 에 없어야 함
    assert "거래량돌파" not in stored
    assert "하이타이트플래그" not in stored


def test_get_strategy_return_history_finds_legacy_korean_snapshot(repo):
    """legacy snapshot 행이 직접 INSERT 된 상태에서도 strategy_id 로 조회 가능.

    _is_weekday 필터 때문에 주말 날짜는 제외되므로, 명시적으로 평일 2건만 사용.
    """
    with repo._db:
        repo._db.execute(
            "INSERT INTO snapshots (date, strategy, return_rate) VALUES (?, ?, ?)",
            ("2026-05-21", "거래량돌파", 0.05),  # Thursday
        )
        repo._db.execute(
            "INSERT INTO snapshots (date, strategy, return_rate) VALUES (?, ?, ?)",
            ("2026-05-22", "거래량돌파", 0.06),  # Friday
        )
    repo._cached_data = None  # invalidate cache
    history = repo.get_strategy_return_history("volume_breakout_live")
    assert len(history) == 2
    assert history[0]["return_rate"] == pytest.approx(0.05)
    assert history[1]["return_rate"] == pytest.approx(0.06)


def test_get_strategy_return_history_unknown_strategy_returns_empty(repo):
    history = repo.get_strategy_return_history("unknown_strategy_xyz")
    assert history == []


# ---------- passthrough unknown ----------

def test_passthrough_unknown_strategy_round_trip(repo):
    repo.log_buy("custom_research_001", "005930", 70000)
    assert repo.is_holding("custom_research_001", "005930") is True
    holds = repo.get_holds_by_strategy("custom_research_001")
    assert len(holds) == 1
    assert holds[0]["strategy"] == "custom_research_001"
