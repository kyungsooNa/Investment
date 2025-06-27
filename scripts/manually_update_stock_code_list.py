# scripts/manually_update_stock_codes.py

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from utils.stock_info_updater import save_stock_code_list

if __name__ == "__main__":
    save_stock_code_list(force_update=True)
