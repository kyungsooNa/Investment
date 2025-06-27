import pandas as pd

class StockCodeNameResolver:
    def __init__(self, csv_path="data/stock_code_list.csv"):
        self.df = pd.read_csv(csv_path, dtype=str)
        self.code_to_name = dict(zip(self.df['code'], self.df['name']))
        self.name_to_code = dict(zip(self.df['name'], self.df['code']))

    def get_name(self, code):
        return self.code_to_name.get(code)

    def get_code(self, name):
        return self.name_to_code.get(name)
