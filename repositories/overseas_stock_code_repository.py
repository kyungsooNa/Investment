# repositories/overseas_stock_code_repository.py

import os
import sqlite3
import pandas as pd
from services.overseas_stock_sync_service import save_overseas_stock_code_list

TABLE_NAME = "overseas_stocks"


def _write_minimal_db(db_path: str, logger=None):
    """빈/손상된 DB 대신 최소 유효 DB를 써서 앱이 시작되도록 합니다."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} (심볼 TEXT, 종목명 TEXT, 거래소 TEXT)"
        )
        conn.execute(
            f"INSERT INTO {TABLE_NAME} VALUES (?, ?, ?)",
            ("(NONE)", "(종목목록 없음)", "NASD"),
        )
        conn.commit()
    finally:
        conn.close()
    if logger:
        logger.warning(f"해외 종목코드 DB가 비어 있어 최소 파일로 생성했습니다: {db_path}")


class OverseasStockCodeRepository:
    """
    해외(미국) 심볼 ↔ 종목명/거래소 조회를 제공하는 SQLite 기반 유틸리티 클래스.
    국내 StockCodeRepository와 동일한 생성/복구 패턴을 따른다.
    """

    def __init__(self, db_path=None, logger=None):
        self.logger = logger
        if db_path is None:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            db_path = os.path.join(root, "data", "overseas_stock_code_list.db")
        self._db_path = db_path

        if not os.path.exists(db_path):
            if self.logger:
                self.logger.info(f"🔍 해외 종목코드 DB 파일 없음. 생성 시작: {db_path}")
            try:
                save_overseas_stock_code_list(force_update=True)
                if self.logger:
                    self.logger.info("✅ 해외 종목코드 DB 파일 생성 완료.")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ 해외 종목코드 DB 파일 생성 실패: {e}")
                raise e

        self._load_data()

    def _load_data(self):
        try:
            self.df = self._read_df()
            if self.df.empty or len(self.df.columns) == 0 or (
                len(self.df) == 1 and self.df.iloc[0]["심볼"] == "(NONE)"
            ):
                raise ValueError("DB 테이블이 비어있거나 최소 DB 상태입니다.")
            self._build_index()
            if self.logger:
                self.logger.info(f"🔄 해외 종목코드 DB 로드 완료: {self._db_path}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"⚠️ 해외 종목코드 DB 갱신/복구 시도 중 (사유: {e})")
            try:
                if os.path.exists(self._db_path):
                    os.remove(self._db_path)
            except Exception as remove_err:
                if self.logger:
                    self.logger.error(f"❌ 손상된 해외 DB 파일 삭제 실패: {remove_err}")
            try:
                save_overseas_stock_code_list(force_update=True)
                self.df = self._read_df()
                if self.df.empty or len(self.df.columns) == 0 or (
                    len(self.df) == 1 and self.df.iloc[0]["심볼"] == "(NONE)"
                ):
                    raise ValueError("DB 테이블이 비어있거나 최소 DB 상태입니다.")
                self._build_index()
                if self.logger:
                    self.logger.info(f"🔄 해외 종목코드 DB 로드 완료: {self._db_path}")
            except Exception:
                if self.logger:
                    self.logger.warning("해외 종목 갱신 실패. 최소 DB로 앱을 시작합니다.")
                _write_minimal_db(self._db_path, self.logger)
                self.df = self._read_df()
                self._build_index()

    def _read_df(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            return pd.read_sql(f"SELECT * FROM {TABLE_NAME}", conn, dtype={"심볼": str})
        finally:
            conn.close()

    def _build_index(self):
        self.symbol_to_meta = {
            str(row["심볼"]): {"name": row["종목명"], "exchange": row["거래소"]}
            for _, row in self.df.iterrows()
        }

    def all_symbols(self) -> list:
        """전 심볼 리스트 반환 (클라이언트 자동완성용)."""
        return [
            {"s": symbol, "n": meta["name"], "e": meta["exchange"]}
            for symbol, meta in self.symbol_to_meta.items()
        ]

    def search(self, keyword: str, limit: int = 20) -> list:
        """심볼 prefix(대소문자 무시) 또는 종목명 부분 일치 검색."""
        kw = keyword.strip()
        if not kw:
            return []
        kw_upper = kw.upper()
        kw_lower = kw.lower()
        results = []
        for symbol, meta in self.symbol_to_meta.items():
            name = meta["name"] or ""
            if symbol.upper().startswith(kw_upper) or kw_lower in name.lower():
                results.append({"s": symbol, "n": name, "e": meta["exchange"]})
                if len(results) >= limit:
                    break
        return results
