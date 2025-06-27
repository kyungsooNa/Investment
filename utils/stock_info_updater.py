# utils/stock_info_updater.py

import pandas as pd
import json
import os
from pykrx import stock
from datetime import datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")
CSV_FILE_PATH = os.path.join(DATA_DIR, "stock_code_list.csv")
METADATA_PATH = os.path.join(DATA_DIR, "metadata.json")


def _save_metadata():
    metadata = {
        "last_updated": datetime.today().strftime("%Y-%m-%d")
    }
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f)


def _load_metadata():
    if not os.path.exists(METADATA_PATH):
        return None
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _needs_update(max_age_days=7):
    metadata = _load_metadata()
    if not metadata:
        return True
    last_updated = datetime.strptime(metadata["last_updated"], "%Y-%m-%d")
    return (datetime.today() - last_updated).days > max_age_days


def save_stock_code_list(force_update=False):
    """
    종목 코드 리스트 저장 (CSV + 메타데이터).
    force_update=True일 경우 날짜와 무관하게 업데이트.
    """
    if not force_update and not _needs_update():
        print("✅ 최근 7일 이내에 이미 업데이트됨. 업데이트 생략.")
        return

    today = datetime.today().strftime("%Y%m%d")

    kospi = stock.get_market_ticker_list(today, market="KOSPI")
    kosdaq = stock.get_market_ticker_list(today, market="KOSDAQ")

    data = []
    for code in kospi:
        data.append({
            "종목코드": code,
            "종목명": stock.get_market_ticker_name(code),
            "시장구분": "KOSPI"
        })
    for code in kosdaq:
        data.append({
            "종목코드": code,
            "종목명": stock.get_market_ticker_name(code),
            "시장구분": "KOSDAQ"
        })

    df = pd.DataFrame(data)
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(CSV_FILE_PATH, index=False, encoding="utf-8-sig")
    _save_metadata()
    print(f"🟢 {len(df)}개 종목 저장 완료: {CSV_FILE_PATH}")


def load_stock_code_list():
    return pd.read_csv(CSV_FILE_PATH, dtype={"종목코드": str})
