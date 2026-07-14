from unittest.mock import AsyncMock, MagicMock

import pytest

from services.dart_disclosure_client import (
    DartApiError,
    DartDisclosureClient,
)


def _response(payload):
    response = MagicMock()
    response.raise_for_status = lambda: None
    response.json.return_value = payload
    return response


async def test_fetch_disclosures_uses_official_list_contract_and_parses_rows():
    http_client = AsyncMock()
    http_client.get.return_value = _response(
        {
            "status": "000",
            "message": "정상",
            "page_no": 1,
            "page_count": 100,
            "total_count": 1,
            "total_page": 1,
            "list": [
                {
                    "corp_cls": "Y",
                    "corp_name": "삼성전자",
                    "corp_code": "00126380",
                    "stock_code": "005930",
                    "report_nm": "단일판매ㆍ공급계약체결",
                    "rcept_no": "20260714001234",
                    "flr_nm": "삼성전자",
                    "rcept_dt": "20260714",
                    "rm": "유",
                }
            ],
        }
    )
    client = DartDisclosureClient("secret", http_client=http_client, timeout_sec=5)

    page = await client.fetch_disclosures("20260714", page_no=1)

    assert page.total_page == 1
    assert page.items[0].stock_code == "005930"
    assert page.items[0].receipt_no == "20260714001234"
    assert page.items[0].viewer_url.endswith("rcpNo=20260714001234")
    params = http_client.get.await_args.kwargs["params"]
    assert params == {
        "crtfc_key": "secret",
        "bgn_de": "20260714",
        "end_de": "20260714",
        "last_reprt_at": "N",
        "sort": "date",
        "sort_mth": "desc",
        "page_no": 1,
        "page_count": 100,
    }


async def test_no_data_status_returns_empty_page():
    http_client = AsyncMock()
    http_client.get.return_value = _response({"status": "013", "message": "조회된 데이타가 없습니다."})
    client = DartDisclosureClient("secret", http_client=http_client)

    page = await client.fetch_disclosures("20260714")

    assert page.items == []
    assert page.total_page == 0


async def test_api_error_does_not_expose_key():
    http_client = AsyncMock()
    http_client.get.return_value = _response({"status": "020", "message": "요청 제한 초과"})
    client = DartDisclosureClient("super-secret", http_client=http_client)

    with pytest.raises(DartApiError) as exc_info:
        await client.fetch_disclosures("20260714")

    assert exc_info.value.status == "020"
    assert "super-secret" not in str(exc_info.value)
