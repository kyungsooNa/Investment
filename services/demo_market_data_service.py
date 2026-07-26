"""외부 broker와 분리된 공개 데모 데이터 서비스."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path


class DemoMarketDataService:
    def __init__(self, data_path: str | Path | None = None):
        path = (
            Path(data_path)
            if data_path is not None
            else Path(__file__).resolve().parents[1] / "data" / "demo" / "sample_data.json"
        )
        with path.open("r", encoding="utf-8") as stream:
            self._data = json.load(stream)
        self._orders: list[dict] = []

    def get_stock_price(self, code: str) -> dict:
        stock = self._data.get("stocks", {}).get(str(code))
        if stock is None:
            return {
                "rt_cd": "1",
                "msg1": "데모 데이터에 없는 종목입니다.",
                "data": None,
            }
        result = copy.deepcopy(stock)
        result["demo"] = True
        return {"rt_cd": "0", "msg1": "성공", "data": result}

    def get_balance(self, *, exchange: str = "KRX") -> dict:
        data = copy.deepcopy(self._data.get("balance", {}))
        return {
            "rt_cd": "0",
            "msg1": "성공",
            "data": data,
            "account_info": {
                "number": "DEMO-0000",
                "type": "데모",
                "exchange": exchange,
            },
        }

    def get_virtual_history(self) -> dict:
        return copy.deepcopy(self._data.get("virtual_history", {"trades": []}))

    def place_order(self, *, code: str, side: str, qty: str, price: str) -> dict:
        order_no = f"DEMO-{len(self._orders) + 1:04d}"
        order = {
            "ord_no": order_no,
            "code": str(code),
            "side": str(side),
            "qty": str(qty),
            "price": str(price),
            "status": "RECORDED",
            "demo": True,
            "ordered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._orders.append(order)
        return {
            "rt_cd": "0",
            "msg1": "데모 주문이 기록되었습니다.",
            "data": copy.deepcopy(order),
        }

    def get_orders(self) -> list[dict]:
        return copy.deepcopy(self._orders)
