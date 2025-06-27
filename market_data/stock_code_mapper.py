# market_data/stock_code_mapper.py

import os
import pandas as pd


class StockCodeMapper:
    """
    종목코드 ↔ 종목명 변환 기능을 제공하는 CSV 기반 유틸리티 클래스.
    """
    def __init__(self, csv_path=None, logger=None):
        self.logger = logger
        if csv_path is None:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            csv_path = os.path.join(root, "data", "stock_code_list.csv")

        try:
            self.df = pd.read_csv(csv_path, dtype={"종목코드": str})
            self.code_to_name = dict(zip(self.df["종목코드"], self.df["종목명"]))
            self.name_to_code = dict(zip(self.df["종목명"], self.df["종목코드"]))
            if self.logger:
                self.logger.info(f"🔄 종목코드 매핑 CSV 로드 완료: {csv_path}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 종목코드 매핑 CSV 로드 실패: {e}")
            raise e

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
