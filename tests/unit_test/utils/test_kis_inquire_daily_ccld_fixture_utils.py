import argparse
import json

import pytest

from utils import kis_inquire_daily_ccld_fixture_utils as fixture_utils
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


@pytest.mark.parametrize(
    "payload",
    [
        [{"odno": "A0001"}],
        {"output1": [{"odno": "A0001"}]},
        {"data": [{"odno": "A0001"}]},
    ],
)
def test_extract_inquire_daily_ccld_output1_rows_supports_known_shapes(payload):
    rows = extract_inquire_daily_ccld_output1_rows(payload)

    assert rows == [{"odno": "A0001"}]
    assert rows[0] is not payload


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"output2": []}, "Could not find output1 rows"),
        ("not-json", "Unsupported payload type"),
        ([{"odno": "A0001"}, "bad-row"], "output1 row #2 is not a dict"),
        ([], "output1 rows are empty"),
    ],
)
def test_extract_inquire_daily_ccld_output1_rows_rejects_invalid_payloads(payload, message):
    with pytest.raises(ValueError, match=message):
        extract_inquire_daily_ccld_output1_rows(payload)


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


def test_sanitize_inquire_daily_ccld_row_masks_uppercase_fields_and_skips_blank_values():
    sanitized = sanitize_inquire_daily_ccld_row(
        {
            "ODNO": "A0001",
            "ORGN_ODNO": "",
            "PDNO": "005930",
            "pdno": None,
        },
        row_index=7,
        mask_stock_code=True,
    )

    assert sanitized == {
        "ODNO": "0000000007",
        "ORGN_ODNO": "",
        "PDNO": "900007",
        "pdno": None,
    }


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


def test_build_inquire_daily_ccld_fixture_document_can_keep_raw_rows_and_unknown_side():
    document = build_inquire_daily_ccld_fixture_document(
        [
            {
                "odno": "A0001",
                "pdno": "005930",
                "ord_qty": "3",
                "tot_ccld_qty": "0",
                "rmn_qty": "3",
                "avg_prvs": "0",
                "ord_dt": "20260424",
                "ord_tmd": "090001",
            }
        ],
        fixture_name="raw_capture",
        tr_id="TTTC0081R",
        description="raw rows",
        sanitize=False,
    )

    assert document["description"] == "raw rows"
    assert document["rows"][0]["case"] == "submitted_unknown_1"
    assert document["rows"][0]["row"]["odno"] == "A0001"
    assert document["rows"][0]["expected"]["side"] is None
    assert document["rows"][0]["expected"]["broker_order_no"] == "A0001"


def test_save_load_and_discover_fixture_documents(tmp_path):
    document = {"fixture_name": "one", "rows": []}
    output_path = tmp_path / "nested" / "inquire_daily_ccld_output1_one.json"
    ignored_path = tmp_path / "nested" / "other.json"

    saved_path = fixture_utils.save_fixture_document(document, output_path)
    ignored_path.write_text("{}", encoding="utf-8")

    assert saved_path == output_path
    assert fixture_utils.load_fixture_document(saved_path) == document
    assert json.loads(output_path.read_text(encoding="utf-8")) == document
    assert list(fixture_utils.discover_inquire_daily_ccld_fixture_documents(tmp_path / "nested")) == [
        (output_path, document)
    ]


def test_default_tr_id_returns_mode_specific_value():
    assert fixture_utils._default_tr_id("paper") == "VTTC0081R"
    assert fixture_utils._default_tr_id("real") == "TTTC0081R"


def test_build_parser_parses_generation_options():
    parser = fixture_utils._build_parser()

    args = parser.parse_args(
        [
            "--input",
            "raw.json",
            "--output",
            "fixture.json",
            "--fixture-name",
            "fixture_name",
            "--description",
            "desc",
            "--mode",
            "real",
            "--tr-id",
            "CUSTOM",
            "--mask-stock-code",
            "--keep-raw",
            "--start-date",
            "20260424",
            "--end-date",
            "20260425",
            "--side-code",
            "01",
            "--stock-code",
            "005930",
            "--ccld-dvsn",
            "02",
            "--order-no",
            "A0001",
            "--exchange",
            "NXT",
        ]
    )

    assert args.input == "raw.json"
    assert args.output == "fixture.json"
    assert args.fixture_name == "fixture_name"
    assert args.description == "desc"
    assert args.mode == "real"
    assert args.tr_id == "CUSTOM"
    assert args.mask_stock_code is True
    assert args.keep_raw is True
    assert args.start_date == "20260424"
    assert args.end_date == "20260425"
    assert args.side_code == "01"
    assert args.stock_code == "005930"
    assert args.ccld_dvsn == "02"
    assert args.order_no == "A0001"
    assert args.exchange == "NXT"


def test_main_parses_args_and_runs_async_main(monkeypatch):
    parsed_args = argparse.Namespace(output="fixture.json")
    parser = argparse.Namespace(parse_args=lambda: parsed_args)
    observed = {}

    monkeypatch.setattr(fixture_utils, "_build_parser", lambda: parser)
    monkeypatch.setattr(fixture_utils, "_async_main", lambda args: ("async-main", args))

    def fake_run(awaitable):
        observed["awaitable"] = awaitable
        return 9

    monkeypatch.setattr(fixture_utils.asyncio, "run", fake_run)

    assert fixture_utils.main() == 9
    assert observed["awaitable"] == ("async-main", parsed_args)


async def test_close_client_sessions_closes_each_session_once():
    class FakeSession:
        def __init__(self):
            self.close_count = 0

        async def aclose(self):
            self.close_count += 1

    class FakeApi:
        def __init__(self, session):
            self._async_session = session

    shared_session = FakeSession()
    unique_session = FakeSession()
    client = argparse.Namespace(
        _quotations=FakeApi(shared_session),
        _account=FakeApi(shared_session),
        _trading=FakeApi(unique_session),
    )

    await fixture_utils._close_client_sessions(client)

    assert shared_session.close_count == 1
    assert unique_session.close_count == 1


async def test_async_main_generates_fixture_from_input_file(tmp_path, capsys):
    input_path = tmp_path / "raw.json"
    output_path = tmp_path / "generated" / "fixture.json"
    input_path.write_text(
        json.dumps(
            {
                "output1": [
                    {
                        "odno": "A0009",
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "02",
                        "ord_qty": "1",
                        "tot_ccld_qty": "0",
                        "rmn_qty": "1",
                        "avg_prvs": "0",
                        "ord_dt": "20260424",
                        "ord_tmd": "090001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        input=str(input_path),
        output=str(output_path),
        fixture_name=None,
        description="from input",
        mode="real",
        tr_id=None,
        keep_raw=True,
        mask_stock_code=False,
    )

    result = await fixture_utils._async_main(args)

    document = fixture_utils.load_fixture_document(output_path)
    captured = capsys.readouterr()
    assert result == 0
    assert document["fixture_name"] == "fixture"
    assert document["tr_id"] == "TTTC0081R"
    assert document["rows"][0]["row"]["odno"] == "A0009"
    assert "rows: 1, tr_id: TTTC0081R" in captured.out
