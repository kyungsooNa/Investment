# repositories/stock_code_mapper.py

import os
import sqlite3
import pandas as pd
from services.stock_sync_service import save_stock_code_list

TABLE_NAME = "stocks"


def _write_minimal_db(db_path: str, logger=None):
    """빈/손상된 DB 대신 최소 유효 DB를 써서 앱이 시작되도록 합니다."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
        conn.execute(
            f"CREATE TABLE {TABLE_NAME} (종목코드 TEXT, 종목명 TEXT, 시장구분 TEXT)"
        )
        conn.execute(
            f"INSERT INTO {TABLE_NAME} VALUES (?, ?, ?)",
            ("000000", "(종목목록 없음)", ""),
        )
        conn.commit()
    finally:
        conn.close()
    if logger:
        logger.warning(f"종목코드 DB가 비어 있어 최소 파일로 생성했습니다. 나중에 스크립트로 갱신하세요: {db_path}")


class StockCodeRepository:
    """
    종목코드 ↔ 종목명 변환 기능을 제공하는 SQLite 기반 유틸리티 클래스.
    """
    def __init__(self, db_path=None, logger=None):
        self.logger = logger
        if db_path is None:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            db_path = os.path.join(root, "data", "stock_code_list.db")
        self._db_path = db_path

        # DB 파일이 없으면 생성 시도
        if not os.path.exists(db_path):
            if self.logger:
                self.logger.info(f"🔍 종목코드 매핑 DB 파일 없음. 생성 시작: {db_path}")
            try:
                save_stock_code_list(force_update=True)
                if self.logger:
                    self.logger.info("✅ 종목코드 매핑 DB 파일 생성 완료.")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ 종목코드 매핑 DB 파일 생성 실패: {e}")
                raise e

        self._load_data()

    def _load_data(self):
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                self.df = pd.read_sql(
                    f"SELECT * FROM {TABLE_NAME}", conn, dtype={"종목코드": str}
                )
            finally:
                conn.close()

            if self.df.empty or len(self.df.columns) == 0 or (len(self.df) == 1 and self.df.iloc[0]["종목코드"] == "000000"):
                raise ValueError("DB 테이블이 비어있거나 최소 DB 상태입니다.")

            self.code_to_name = dict(zip(self.df["종목코드"], self.df["종목명"]))
            self.name_to_code = dict(zip(self.df["종목명"], self.df["종목코드"]))
            if self.logger:
                self.logger.info(f"🔄 종목코드 매핑 DB 로드 완료: {self._db_path}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"⚠️ 종목코드 DB 갱신/복구 시도 중 (사유: {e})")
            
            # 손상된 기존 파일 삭제 시도
            try:
                if os.path.exists(self._db_path):
                    os.remove(self._db_path)
                    if self.logger:
                        self.logger.info(f"🗑️ 손상된 DB 파일 삭제 완료: {self._db_path}")
            except Exception as remove_err:
                if self.logger:
                    self.logger.error(f"❌ 손상된 DB 파일 삭제 실패 (파일 점유/권한 문제 등): {remove_err}")

            try:
                save_stock_code_list(force_update=True)
                conn = sqlite3.connect(self._db_path)
                try:
                    self.df = pd.read_sql(
                        f"SELECT * FROM {TABLE_NAME}", conn, dtype={"종목코드": str}
                    )
                finally:
                    conn.close()
                if self.df.empty or len(self.df.columns) == 0 or (len(self.df) == 1 and self.df.iloc[0]["종목코드"] == "000000"):
                    raise ValueError("DB 테이블이 비어있거나 최소 DB 상태입니다.")
                self.code_to_name = dict(zip(self.df["종목코드"], self.df["종목명"]))
                self.name_to_code = dict(zip(self.df["종목명"], self.df["종목코드"]))
                if self.logger:
                    self.logger.info(f"🔄 종목코드 매핑 DB 로드 완료: {self._db_path}")
            except Exception:
                if self.logger:
                    self.logger.warning("갱신 실패. 최소 DB로 앱을 시작합니다.")
                _write_minimal_db(self._db_path, self.logger)
                conn = sqlite3.connect(self._db_path)
                try:
                    self.df = pd.read_sql(
                        f"SELECT * FROM {TABLE_NAME}", conn, dtype={"종목코드": str}
                    )
                finally:
                    conn.close()
                self.code_to_name = dict(zip(self.df["종목코드"], self.df["종목명"]))
                self.name_to_code = dict(zip(self.df["종목명"], self.df["종목코드"]))

    def get_name_by_code(self, code: str) -> str:
        name = self.code_to_name.get(code, "")
        if not name and self.logger:
            self.logger.warning(f"❗ 종목명 없음: {code}")
        return name

    def get_code_by_name(self, name: str) -> str:
        code = self.name_to_code.get(name, "")
        if not code and self.logger:
            self.logger.warning(f"❗ 종목코드 없음: {name}")
        return code

    def search_by_name(self, keyword: str, limit: int = 20) -> list:
        """종목명 부분 일치 검색. [{"code": "005930", "name": "삼성전자"}, ...] 형태로 반환."""
        keyword_lower = keyword.lower()
        results = []
        for name, code in self.name_to_code.items():
            if keyword_lower in name.lower():
                results.append({"code": code, "name": name})
                if len(results) >= limit:
                    break
        return results

    def get_kosdaq_codes(self) -> list:
        """코스닥 시장 종목코드 리스트 반환."""
        if "시장구분" not in self.df.columns:
            return []
        return self.df[self.df["시장구분"] == "KOSDAQ"]["종목코드"].tolist()

    def is_kosdaq(self, code: str) -> bool:
        """해당 종목코드가 코스닥 시장인지 확인."""
        if "시장구분" not in self.df.columns:
            return False
        row = self.df[self.df["종목코드"] == code]
        return not row.empty and row.iloc[0]["시장구분"] == "KOSDAQ"
