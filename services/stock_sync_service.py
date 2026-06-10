# utils/stock_info_updater.py

import pandas as pd
import json
import os
import sqlite3
from datetime import datetime
import FinanceDataReader as fdr

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_FILE_PATH = os.path.join(DATA_DIR, "stock_code_list.db")
METADATA_PATH = os.path.join(DATA_DIR, "metadata.json")

# 하위 호환용 (테스트 등에서 참조)
CSV_FILE_PATH = os.path.join(DATA_DIR, "stock_code_list.csv")

TABLE_NAME = "stocks"


def _save_metadata():
    metadata = {
        "last_updated": datetime.today().strftime("%Y-%m-%d")
    }
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


def save_stock_code_list(force_update=False):
    """
    종목 코드 리스트 저장 (SQLite + 메타데이터).
    force_update=True일 경우 날짜와 무관하게 업데이트.
    """
    save_stock_code_list_fdr(force_update=force_update)


def save_stock_code_list_fdr(force_update=False):
    if not force_update and not _needs_update():
        print("✅ 최근 7일 이내에 이미 업데이트됨. 업데이트 생략.")
        return

    try:
        print("🔄 FinanceDataReader를 통해 KRX 종목 목록을 다운로드합니다...")
        # 전체 종목 리스트 가져오기
        df_all = fdr.StockListing('KRX')

        # 1. 필요한 컬럼만 추출 및 이름 변경 (pykrx 형식에 맞춤)
        # FinanceDataReader: Code -> 종목코드, Name -> 종목명
        # MarketId를 통해 시장구분 생성
        df_all = df_all[['Code', 'Name', 'MarketId']].copy()
        
        # MarketId를 KOSPI/KOSDAQ으로 변환 (KONEX 포함 여부는 선택)
        market_map = {
            'STK': 'KOSPI',
            'KSQ': 'KOSDAQ',
            'KNX': 'KONEX'
        }
        df_all['시장구분'] = df_all['MarketId'].map(market_map)
        
        # 컬럼명 최종 변경
        df = df_all.rename(columns={'Code': '종목코드', 'Name': '종목명'})
        
        # 필요한 시장만 필터링 (KONEX를 제외하려면 아래 주석 해제)
        df = df[df['시장구분'].isin(['KOSPI', 'KOSDAQ'])]

        # --- 이후 DB 저장 로직은 기존과 동일 ---
        os.makedirs(DATA_DIR, exist_ok=True)
        df[['종목코드', '종목명', '시장구분']].to_csv(CSV_FILE_PATH, index=False, encoding="utf-8-sig")

        conn = sqlite3.connect(DB_FILE_PATH)
        try:
            df[['종목코드', '종목명', '시장구분']].to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_code ON {TABLE_NAME}(종목코드)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_name ON {TABLE_NAME}(종목명)")
            conn.commit()
        finally:
            conn.close()

        _save_metadata()
        print(f"🟢 {len(df)}개 종목 저장 완료 (FDR 사용): {DB_FILE_PATH}")

    except Exception as e:
        print(f"❌ 데이터 업데이트 실패: {e}")
        raise

def load_stock_code_list():
    conn = sqlite3.connect(DB_FILE_PATH)
    try:
        return pd.read_sql(f"SELECT * FROM {TABLE_NAME}", conn, dtype={"종목코드": str})
    finally:
        conn.close()
