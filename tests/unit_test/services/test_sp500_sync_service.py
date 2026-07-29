"""
sp500_sync_service 단위 테스트 (FDR 네트워크 호출은 전부 mock).
"""
import sqlite3
from unittest.mock import patch

import pandas as pd
import pytest

from services import sp500_sync_service as svc


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "DB_FILE_PATH", str(tmp_path / "sp500_list.db"))
    monkeypatch.setattr(svc, "METADATA_PATH", str(tmp_path / "sp500_metadata.json"))
    return tmp_path


def _listing(rows):
    return pd.DataFrame(rows, columns=["Symbol", "Name", "Sector", "Industry"])


def test_save_writes_symbol_name_sector(tmp_paths):
    listing = _listing([
        ["AAPL", "Apple Inc.", "Information Technology", "Hardware"],
        ["MSFT", "Microsoft", "Information Technology", "Software"],
    ])
    with patch.object(svc.fdr, "StockListing", return_value=listing):
        svc.save_sp500_list(force_update=True)

    df = svc.load_sp500_list()
    assert list(df.columns) == ["심볼", "종목명", "섹터"]
    assert df["심볼"].tolist() == ["AAPL", "MSFT"]
    assert df.loc[0, "섹터"] == "Information Technology"
    assert (tmp_paths / "sp500_metadata.json").exists()


def test_save_drops_null_and_duplicate_symbols(tmp_paths):
    listing = _listing([
        ["AAPL", "Apple Inc.", "IT", "Hardware"],
        ["AAPL", "Apple duplicate", "IT", "Hardware"],
        [None, "이름만 있는 행", "IT", "Hardware"],
    ])
    with patch.object(svc.fdr, "StockListing", return_value=listing):
        svc.save_sp500_list(force_update=True)

    df = svc.load_sp500_list()
    assert df["심볼"].tolist() == ["AAPL"]
    assert df.loc[0, "종목명"] == "Apple Inc."


def test_save_tolerates_listing_without_sector(tmp_paths):
    """FDR 스키마가 바뀌어 Sector 가 빠져도 빈 섹터로 저장한다."""
    listing = pd.DataFrame([["AAPL", "Apple Inc."]], columns=["Symbol", "Name"])
    with patch.object(svc.fdr, "StockListing", return_value=listing):
        svc.save_sp500_list(force_update=True)

    df = svc.load_sp500_list()
    assert df.loc[0, "섹터"] == ""


def test_save_skips_when_recently_updated(tmp_paths):
    listing = _listing([["AAPL", "Apple Inc.", "IT", "Hardware"]])
    with patch.object(svc.fdr, "StockListing", return_value=listing) as mock_listing:
        svc.save_sp500_list(force_update=True)
        assert mock_listing.call_count == 1
        # 방금 저장했으므로 force 없이는 다시 받지 않는다.
        svc.save_sp500_list()
        assert mock_listing.call_count == 1


def test_save_raises_when_download_fails(tmp_paths):
    with patch.object(svc.fdr, "StockListing", side_effect=RuntimeError("network down")):
        with pytest.raises(RuntimeError):
            svc.save_sp500_list(force_update=True)


def test_saved_table_has_symbol_index(tmp_paths):
    listing = _listing([["AAPL", "Apple Inc.", "IT", "Hardware"]])
    with patch.object(svc.fdr, "StockListing", return_value=listing):
        svc.save_sp500_list(force_update=True)

    conn = sqlite3.connect(svc.DB_FILE_PATH)
    try:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")]
    finally:
        conn.close()
    assert "idx_sp500_symbol" in names
