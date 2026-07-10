import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from repositories.streaming_stock_repo import StreamingStockRepo, StreamingType


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def repo(mock_logger):
    return StreamingStockRepo(logger=mock_logger)


def test_init(repo, mock_logger):
    """초기 상태 및 로거 주입 검증"""
    assert repo._logger == mock_logger
    for stream_type in StreamingType:
        assert len(repo.get_desired(stream_type)) == 0
        assert len(repo.get_active(stream_type)) == 0


@pytest.mark.asyncio
async def test_mark_unmark_desired(repo):
    """구독 대상(desired) 추가 및 제거 검증"""
    st = StreamingType.UNIFIED_PRICE
    
    await repo.mark_desired("005930", st)
    assert "005930" in repo.get_desired(st)

    await repo.unmark_desired("005930", st)
    assert "005930" not in repo.get_desired(st)


@pytest.mark.asyncio
async def test_mark_unmark_clear_active(repo):
    """활성 상태(active) 추가, 제거 및 전체 초기화 검증"""
    st = StreamingType.UNIFIED_PRICE
    
    await repo.mark_active("005930", st)
    assert "005930" in repo.get_active(st)
    assert repo.is_active("005930", st) is True

    await repo.mark_inactive("005930", st)
    assert "005930" not in repo.get_active(st)
    assert repo.is_active("005930", st) is False

    await repo.mark_active("000660", st)
    await repo.clear_active(st)
    assert "000660" not in repo.get_active(st)


@pytest.mark.asyncio
async def test_get_pending(repo):
    """대기 상태(pending = desired - active) 검증"""
    st = StreamingType.UNIFIED_PRICE
    
    await repo.mark_desired("005930", st)
    await repo.mark_desired("000660", st)
    await repo.mark_active("005930", st)

    pending = repo.get_pending(st)
    assert "000660" in pending
    assert "005930" not in pending


@pytest.mark.asyncio
async def test_get_status(repo):
    """현재 상태 요약(get_status) 검증"""
    st = StreamingType.UNIFIED_PRICE
    
    await repo.mark_desired("005930", st)
    await repo.mark_active("005930", st)
    await repo.mark_desired("000660", st)

    status = repo.get_status()
    assert "005930" in status[st.value]["desired"]
    assert "005930" in status[st.value]["active"]
    assert "000660" in status[st.value]["pending"]


@pytest.mark.asyncio
async def test_pt_persistence_flow(tmp_path, mock_logger):
    """SQLite DB를 활용한 PROGRAM_TRADING 영속화 흐름 통합 검증"""
    db_path = str(tmp_path / "test_pt.db")

    # 1. repo 설정 및 DB 연결 — 테이블이 없으면 직접 생성한다.
    repo1 = StreamingStockRepo(logger=mock_logger)
    repo1.load_pt_desired_from_db(db_path)
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "pt_subscriptions" in tables
    
    # 2. 구독 대상 추가 — flush 전에는 in-memory만 반영, DB는 미반영
    await repo1.mark_desired("005930", StreamingType.PROGRAM_TRADING)
    await repo1.mark_desired("000660", StreamingType.PROGRAM_TRADING)

    assert "005930" in repo1.get_desired(StreamingType.PROGRAM_TRADING)

    conn = sqlite3.connect(db_path)
    rows_before = {row[0] for row in conn.execute("SELECT code FROM pt_subscriptions").fetchall()}
    conn.close()
    assert len(rows_before) == 0

    # flush 후 DB 반영 확인
    repo1.flush_pt_desired_sync()
    conn = sqlite3.connect(db_path)
    rows = {row[0] for row in conn.execute("SELECT code FROM pt_subscriptions").fetchall()}
    conn.close()
    assert "005930" in rows
    assert "000660" in rows

    # 3. 새로운 repo 객체로 DB에서 복원 검증
    repo2 = StreamingStockRepo(logger=mock_logger)
    repo2.load_pt_desired_from_db(db_path)
    desired2 = repo2.get_desired(StreamingType.PROGRAM_TRADING)
    assert "005930" in desired2
    assert "000660" in desired2

    # 4. 구독 대상 제거 검증 — flush 후 DB 반영
    await repo1.unmark_desired("005930", StreamingType.PROGRAM_TRADING)
    repo1.flush_pt_desired_sync()

    repo3 = StreamingStockRepo(logger=mock_logger)
    repo3.load_pt_desired_from_db(db_path)
    desired3 = repo3.get_desired(StreamingType.PROGRAM_TRADING)
    assert "005930" not in desired3
    assert "000660" in desired3


@pytest.mark.asyncio
async def test_pt_persistence_tracks_subscription_source(tmp_path, mock_logger):
    """PROGRAM_TRADING desired는 수동/프로그램 출처를 함께 저장한다."""
    db_path = str(tmp_path / "test_pt.db")
    repo = StreamingStockRepo(logger=mock_logger)
    repo.load_pt_desired_from_db(db_path)

    await repo.mark_desired("005930", StreamingType.PROGRAM_TRADING, source="manual")
    await repo.mark_desired("000660", StreamingType.PROGRAM_TRADING, source="program")
    repo.flush_pt_desired_sync()

    assert repo.get_pt_subscription_sources() == {
        "005930": "manual",
        "000660": "program",
    }

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT code, source FROM pt_subscriptions").fetchall())
    conn.close()
    assert rows == {
        "005930": "manual",
        "000660": "program",
    }


def test_load_legacy_pt_subscription_table_migrates_source_to_legacy(tmp_path, mock_logger):
    """기존 code-only pt_subscriptions 테이블은 출처 미확인(legacy)으로 이관한다."""
    db_path = str(tmp_path / "test_pt.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE pt_subscriptions (code TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO pt_subscriptions (code) VALUES ('005930')")
    conn.commit()
    conn.close()

    repo = StreamingStockRepo(logger=mock_logger)
    repo.load_pt_desired_from_db(db_path)

    assert repo.get_desired(StreamingType.PROGRAM_TRADING) == {"005930"}
    assert repo.get_pt_subscription_sources() == {"005930": "legacy"}


def test_load_pt_desired_from_db_uses_snapshot_fallback_when_db_empty(tmp_path, mock_logger):
    """기존 스냅샷 구독 종목을 빈 pt_subscriptions 테이블로 1회 이관한다."""
    db_path = str(tmp_path / "test_pt.db")
    repo = StreamingStockRepo(logger=mock_logger)

    repo.load_pt_desired_from_db(db_path, fallback_codes=["005930", "000660", "005930", ""])

    desired = repo.get_desired(StreamingType.PROGRAM_TRADING)
    assert desired == {"005930", "000660"}

    conn = sqlite3.connect(db_path)
    rows = {row[0] for row in conn.execute("SELECT code FROM pt_subscriptions").fetchall()}
    conn.close()
    assert rows == {"005930", "000660"}
    assert repo.get_pt_subscription_sources() == {
        "005930": "legacy",
        "000660": "legacy",
    }


def test_load_pt_desired_from_db_reclassifies_old_manual_rows_without_timestamp(tmp_path, mock_logger):
    """출처 추적 전 manual 행(updated_at NULL)은 legacy로 재분류한다."""
    db_path = str(tmp_path / "test_pt.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE pt_subscriptions (code TEXT PRIMARY KEY, source TEXT, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO pt_subscriptions (code, source, updated_at) VALUES ('005930', 'manual', NULL)"
    )
    conn.commit()
    conn.close()

    repo = StreamingStockRepo(logger=mock_logger)
    repo.load_pt_desired_from_db(db_path)

    assert repo.get_pt_subscription_sources() == {"005930": "legacy"}
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT source FROM pt_subscriptions WHERE code = '005930'").fetchone()[0] == "legacy"
    conn.close()


def test_load_pt_desired_from_db_prefers_existing_db_over_snapshot_fallback(tmp_path, mock_logger):
    """DB에 저장된 구독 상태가 있으면 오래된 스냅샷 구독 코드는 섞지 않는다."""
    db_path = str(tmp_path / "test_pt.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE pt_subscriptions (code TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO pt_subscriptions (code) VALUES ('080220')")
    conn.commit()
    conn.close()

    repo = StreamingStockRepo(logger=mock_logger)
    repo.load_pt_desired_from_db(db_path, fallback_codes=["005930", "000660"])

    assert repo.get_desired(StreamingType.PROGRAM_TRADING) == {"080220"}


def test_load_pt_desired_from_db_failure(mock_logger):
    """DB 복원 실패 시 예외를 로깅하고 앱이 중단되지 않는지 검증"""
    repo = StreamingStockRepo(logger=mock_logger)
    repo.load_pt_desired_from_db("/invalid/path/that/does/not/exist/db.sqlite")
    
    mock_logger.warning.assert_called()
    assert "PT desired DB 복원 실패" in mock_logger.warning.call_args[0][0]


@pytest.mark.asyncio
async def test_persist_pt_desired_execute_failure(tmp_path, mock_logger):
    """flush_pt_desired_sync() 중 DB 예외 발생 시 무시되고 warning 로깅만 남는지 검증"""
    repo = StreamingStockRepo(logger=mock_logger)
    repo._db_conn = MagicMock()
    repo._db_conn.execute.side_effect = Exception("Mock DB Execute Error")

    await repo.mark_desired("005930", StreamingType.PROGRAM_TRADING)

    # mark_desired만으로는 DB 기록 없음 — pending queue에만 적재
    mock_logger.warning.assert_not_called()

    # flush 시 실제 DB 기록 시도 → 예외 발생 → warning 로그
    repo.flush_pt_desired_sync()

    mock_logger.warning.assert_called_once()
    assert "Mock DB Execute Error" in mock_logger.warning.call_args[0][0]
