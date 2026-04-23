from utils.kis_inquire_daily_ccld_fixture_utils import (
    build_inquire_daily_ccld_fixture_document,
    extract_inquire_daily_ccld_output1_rows,
    sanitize_inquire_daily_ccld_row,
)


def test_extract_inquire_daily_ccld_output1_rows_from_res_common_response_shape():
    rows = extract_inquire_daily_ccld_output1_rows(
        {
            "rt_cd": "0",
            "msg1": "OK",
            "data": {
                "output1": [
                    {"odno": "A0001", "pdno": "005930"},
                    {"odno": "A0002", "pdno": "000660"},
                ]
            },
        }
    )

    assert rows == [
        {"odno": "A0001", "pdno": "005930"},
        {"odno": "A0002", "pdno": "000660"},
    ]


def test_sanitize_inquire_daily_ccld_row_masks_order_number_only_by_default():
    sanitized = sanitize_inquire_daily_ccld_row(
        {
            "odno": "A0001",
            "orgn_odno": "A0000",
            "pdno": "005930",
            "ord_qty": "10",
        },
        row_index=3,
    )

    assert sanitized["odno"] == "0000000003"
    assert sanitized["orgn_odno"] == "0000000003"
    assert sanitized["pdno"] == "005930"
    assert sanitized["ord_qty"] == "10"


def test_build_inquire_daily_ccld_fixture_document_builds_expected_cases():
    document = build_inquire_daily_ccld_fixture_document(
        {
            "data": {
                "output1": [
                    {
                        "odno": "A0001",
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "02",
                        "ord_qty": "10",
                        "tot_ccld_qty": "0",
                        "rmn_qty": "10",
                        "avg_prvs": "0",
                        "ord_dt": "20260424",
                        "ord_tmd": "090001",
                    },
                    {
                        "odno": "A0002",
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "01",
                        "ord_qty": "5",
                        "tot_ccld_qty": "5",
                        "rmn_qty": "0",
                        "avg_prvs": "71200",
                        "ord_dt": "20260424",
                        "ord_tmd": "090010",
                    },
                ]
            }
        },
        fixture_name="paper_capture",
        tr_id="VTTC0081R",
    )

    assert document["fixture_name"] == "paper_capture"
    assert document["tr_id"] == "VTTC0081R"
    assert [case["case"] for case in document["rows"]] == [
        "submitted_buy_1",
        "filled_sell_1",
    ]
    assert document["rows"][0]["expected"]["broker_order_no"] == "0000000001"
    assert document["rows"][0]["expected"]["event_state"] == "SUBMITTED"
    assert document["rows"][1]["expected"]["side"] == "SELL"
    assert document["rows"][1]["expected"]["fill_price"] == 71200
