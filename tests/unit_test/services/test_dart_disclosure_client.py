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


def _text_response(text):
    response = MagicMock()
    response.raise_for_status = lambda: None
    response.text = text
    response.content = text.encode("utf-8")
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


async def test_fetch_disclosure_text_loads_viewer_and_extracts_actual_body():
    http_client = AsyncMock()
    http_client.get.side_effect = [
        _text_response(
            'viewDoc("20260720800314", "11482555", "0", "0", "0", "HTML", "");'
        ),
        _text_response(
            """
            <html><body>
              <table>
                <tr><th>주요 내용</th></tr>
                <tr><td>2027년 상반기 하이브리드 본더 전용 공장 가동 예정</td></tr>
              </table>
            </body></html>
            """
        ),
    ]
    client = DartDisclosureClient("secret", http_client=http_client)

    text = await client.fetch_disclosure_text("20260720800314")

    assert "하이브리드 본더 전용 공장" in text
    assert "<table>" not in text
    first_call, second_call = http_client.get.await_args_list
    assert first_call.args[0] == client.MAIN_URL
    assert first_call.kwargs["params"] == {"rcpNo": "20260720800314"}
    assert second_call.args[0] == client.VIEWER_URL
    assert second_call.kwargs["params"]["dcmNo"] == "11482555"


async def test_fetch_disclosure_text_returns_empty_when_viewer_id_is_missing():
    http_client = AsyncMock()
    http_client.get.return_value = _text_response("<html>문서 생성 중</html>")
    client = DartDisclosureClient("secret", http_client=http_client)

    assert await client.fetch_disclosure_text("20260720800314") == ""


async def test_fetch_disclosure_text_decodes_euc_kr_document_as_cp949():
    http_client = AsyncMock()
    main = _text_response(
        'viewDoc("20260720000120", "11480001", "0", "0", "0", "HTML", "");'
    )
    viewer = MagicMock()
    viewer.raise_for_status = lambda: None
    viewer.content = (
        '<html><head><meta charset="euc-kr"></head>'
        "<body>미래에셋증권 주가연계증권 발행</body></html>"
    ).encode("cp949")
    viewer.text = viewer.content.decode("latin-1")
    http_client.get.side_effect = [main, viewer]
    client = DartDisclosureClient("secret", http_client=http_client)

    text = await client.fetch_disclosure_text("20260720000120")

    assert "미래에셋증권 주가연계증권 발행" in text
    assert "�" not in text


async def test_fetch_disclosure_text_uses_real_viewer_section_parameters():
    http_client = AsyncMock()
    main = _text_response(
        """
        node1['rcpNo'] = "20260720000120";
        node1['dcmNo'] = "11482504";
        node1['eleId'] = "1";
        node1['offset'] = "787";
        node1['length'] = "6248";
        node1['dtd'] = "dart4.xsd";
        """
    )
    viewer = _text_response("<html><body>정상 한글 본문</body></html>")
    http_client.get.side_effect = [main, viewer]
    client = DartDisclosureClient("secret", http_client=http_client)

    text = await client.fetch_disclosure_text("20260720000120")

    assert text == "정상 한글 본문"
    params = http_client.get.await_args_list[1].kwargs["params"]
    assert params == {
        "rcpNo": "20260720000120",
        "dcmNo": "11482504",
        "eleId": "1",
        "offset": "787",
        "length": "6248",
        "dtd": "dart4.xsd",
    }
