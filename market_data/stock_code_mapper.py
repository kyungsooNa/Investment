# market_data/stock_code_mapper.py

import os
import pandas as pd
from utils.stock_info_updater import save_stock_code_list # Import the function


class StockCodeMapper:
    """
    종목코드 ↔ 종목명 변환 기능을 제공하는 CSV 기반 유틸리티 클래스.
    """
    def __init__(self, csv_path=None, logger=None):
        self.logger = logger
        if csv_path is None:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            csv_path = os.path.join(root, "data", "stock_code_list.csv")

        # Check if the CSV file exists, if not, create it
        if not os.path.exists(csv_path):
            if self.logger:
                self.logger.info(f"🔍 종목코드 매핑 CSV 파일 없음. 생성 시작: {csv_path}")
            try:
                save_stock_code_list(force_update=True) # Call save function to create the CSV
                if self.logger:
                    self.logger.info("✅ 종목코드 매핑 CSV 파일 생성 완료.")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ 종목코드 매핑 CSV 파일 생성 실패: {e}")
                raise e

        try:
            self.df = pd.read_csv(csv_path, dtype={"종목코드": str})
            if self.df.empty or self.df.columns.empty:
                raise ValueError("stock_code_list.csv가 비어있거나 컬럼이 없습니다.")

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
