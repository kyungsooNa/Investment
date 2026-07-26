from services.demo_market_data_service import DemoMarketDataService


def test_demo_market_data_returns_isolated_samples_and_records_virtual_order(
    tmp_path,
):
    data_path = tmp_path / "demo.json"
    data_path.write_text(
        """
{
  "stocks": {"005930": {"code": "005930", "name": "삼성전자", "stck_prpr": "70000"}},
  "balance": {"output1": [], "output2": [{"tot_evlu_amt": "10000000"}]},
  "virtual_history": {"trades": [{"code": "005930", "status": "HOLD"}]}
}
""".strip(),
        encoding="utf-8",
    )
    service = DemoMarketDataService(data_path=data_path)

    price = service.get_stock_price("005930")
    balance = service.get_balance(exchange="KRX")
    history = service.get_virtual_history()
    order = service.place_order(
        code="005930", side="buy", qty="2", price="70000"
    )

    assert price["data"]["stck_prpr"] == "70000"
    assert balance["data"]["output2"][0]["tot_evlu_amt"] == "10000000"
    assert history["trades"][0]["code"] == "005930"
    assert order["data"]["demo"] is True
    assert service.get_orders()[0]["code"] == "005930"

    price["data"]["stck_prpr"] = "1"
    assert service.get_stock_price("005930")["data"]["stck_prpr"] == "70000"
