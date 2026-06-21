# services/overseas_stock_sync_service.py
#
# 해외(미국) 종목 심볼 목록 동기화 서비스.
# FinanceDataReader로 NASDAQ/NYSE/AMEX 상장목록을 받아 SQLite DB로 저장한다.
# 국내용 stock_sync_service.py와 동일한 메타데이터/캐시 패턴을 따른다.

import json
import logging
import os
import sqlite3
from datetime import datetime
import pandas as pd
import FinanceDataReader as fdr

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_FILE_PATH = os.path.join(DATA_DIR, "overseas_stock_code_list.db")
METADATA_PATH = os.path.join(DATA_DIR, "overseas_metadata.json")

TABLE_NAME = "overseas_stocks"

# FDR StockListing 시장명 → OverseasExchange enum 코드 매핑
_MARKET_TO_EXCHANGE = {
    "NASDAQ": "NASD",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
}


def _save_metadata():
    metadata = {"last_updated": datetime.today().strftime("%Y-%m-%d")}
    with open(METADATA_PATH, "w", encoding="utf-8-sig") as f:
        json.dump(metadata, f)


def _load_metadata():
    if not os.path.exists(METADATA_PATH):
        return None
    with open(METADATA_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _needs_update(max_age_days=7):
    metadata = _load_metadata()
    if not metadata:
        return True
    last_updated = datetime.strptime(metadata["last_updated"], "%Y-%m-%d")
    return (datetime.today() - last_updated).days > max_age_days


def save_overseas_stock_code_list(force_update=False):
    """
    해외(미국) 종목 심볼 리스트 저장 (SQLite + 메타데이터).
    force_update=True일 경우 날짜와 무관하게 업데이트.
    """
    if not force_update and not _needs_update():
        logger.info("✅ 해외 종목: 최근 7일 이내에 이미 업데이트됨. 업데이트 생략.")
        return

    try:
        logger.info("🔄 FinanceDataReader를 통해 해외(NASDAQ/NYSE/AMEX) 종목 목록을 다운로드합니다...")
        frames = []
        for market, exchange in _MARKET_TO_EXCHANGE.items():
            df_market = fdr.StockListing(market)[["Symbol", "Name"]].copy()
            df_market["거래소"] = exchange
            frames.append(df_market)

        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns={"Symbol": "심볼", "Name": "종목명"})
        df = df[["심볼", "종목명", "거래소"]]
        # 심볼 누락/중복 정리 (중복 시 첫 거래소 유지)
        df = df.dropna(subset=["심볼"])
        df = df.drop_duplicates(subset=["심볼"], keep="first")

        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_FILE_PATH)
        try:
            df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_overseas_symbol ON {TABLE_NAME}(심볼)")
            conn.commit()
        finally:
            conn.close()

        _save_metadata()
        logger.info(f"🟢 {len(df)}개 해외 종목 저장 완료 (FDR 사용): {DB_FILE_PATH}")

    except Exception as e:
        logger.error(f"❌ 해외 종목 데이터 업데이트 실패: {e}")
        raise


def load_overseas_stock_code_list():
    conn = sqlite3.connect(DB_FILE_PATH)
    try:
        return pd.read_sql(f"SELECT * FROM {TABLE_NAME}", conn, dtype={"심볼": str})
    finally:
        conn.close()
