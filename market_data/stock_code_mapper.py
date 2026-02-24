# market_data/stock_code_mapper.py

import os
import pandas as pd
from utils.stock_info_updater import save_stock_code_list  # Import the function


def _write_minimal_csv(csv_path: str, logger=None):
    """빈/손상된 CSV 대신 최소 유효 CSV를 써서 앱이 시작되도록 합니다."""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    minimal = pd.DataFrame([{"종목코드": "000000", "종목명": "(종목목록 없음)"}])
    minimal.to_csv(csv_path, index=False, encoding="utf-8-sig")
    if logger:
        logger.warning(f"종목코드 CSV가 비어 있어 최소 파일로 생성했습니다. 나중에 스크립트로 갱신하세요: {csv_path}")


class StockCodeMapper:
    """
    종목코드 ↔ 종목명 변환 기능을 제공하는 CSV 기반 유틸리티 클래스.
    """
    def __init__(self, csv_path=None, logger=None):
        self.logger = logger
        if csv_path is None:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            csv_path = os.path.join(root, "data", "stock_code_list.csv")
        self._csv_path = csv_path

        # Check if the CSV file exists, if not, create it
        if not os.path.exists(csv_path):
            if self.logger:
                self.logger.info(f"🔍 종목코드 매핑 CSV 파일 없음. 생성 시작: {csv_path}")
            try:
                save_stock_code_list(force_update=True)
                if self.logger:
                    self.logger.info("✅ 종목코드 매핑 CSV 파일 생성 완료.")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ 종목코드 매핑 CSV 파일 생성 실패: {e}")
                raise e

        self._load_df()

    def _load_df(self):
        try:
            self.df = pd.read_csv(self._csv_path, dtype={"종목코드": str})
            if self.df.empty or len(self.df.columns) == 0:
                raise ValueError("CSV가 비어있거나 컬럼이 없습니다.")
            self.code_to_name = dict(zip(self.df["종목코드"], self.df["종목명"]))
            self.name_to_code = dict(zip(self.df["종목명"], self.df["종목코드"]))
            if self.logger:
                self.logger.info(f"🔄 종목코드 매핑 CSV 로드 완료: {self._csv_path}")
        except Exception as e:
            err_msg = str(e)
            if "No columns to parse" not in err_msg and "비어" not in err_msg:
                if self.logger:
                    self.logger.error(f"❌ 종목코드 매핑 CSV 로드 실패: {e}")
                raise
            # CSV가 비어 있는 경우: 갱신 시도 후 실패하면 최소 CSV로 시작
            if self.logger:
                self.logger.warning("종목코드 CSV가 비어 있음. 갱신 시도 후 재시도합니다.")
            try:
                save_stock_code_list(force_update=True)
                self.df = pd.read_csv(self._csv_path, dtype={"종목코드": str})
                if self.df.empty or len(self.df.columns) == 0:
                    raise ValueError("CSV가 비어있거나 컬럼이 없습니다.")
                self.code_to_name = dict(zip(self.df["종목코드"], self.df["종목명"]))
                self.name_to_code = dict(zip(self.df["종목명"], self.df["종목코드"]))
                if self.logger:
                    self.logger.info(f"🔄 종목코드 매핑 CSV 로드 완료: {self._csv_path}")
            except Exception:
                if self.logger:
                    self.logger.warning("갱신 실패. 최소 CSV로 앱을 시작합니다.")
                _write_minimal_csv(self._csv_path, self.logger)
                self.df = pd.read_csv(self._csv_path, dtype={"종목코드": str})
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
