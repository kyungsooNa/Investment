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


async def test_fetch_disclosure_text_rejects_a_blank_receipt_no():
    http_client = AsyncMock()
    client = DartDisclosureClient("secret", http_client=http_client)

    assert await client.fetch_disclosure_text("  ") == ""
    http_client.get.assert_not_awaited()


async def test_fetch_disclosure_text_falls_back_to_the_dcm_no_when_sections_are_absent():
    """뷰어 섹션 목록을 못 찾으면 dcmNo 하나로 단일 섹션을 구성한다."""
    http_client = AsyncMock()
    main = _text_response('viewDoc("20260720000120", "11480001", "0", "0", "0", "HTML", "");')
    viewer = _text_response("<html><body>본문 텍스트</body></html>")
    http_client.get.side_effect = [main, viewer]
    client = DartDisclosureClient("secret", http_client=http_client)

    assert "본문 텍스트" in await client.fetch_disclosure_text("20260720000120")


async def test_fetch_disclosure_text_stops_once_the_char_budget_is_reached():
    http_client = AsyncMock()
    main = _text_response('viewDoc("20260720000120", "11480001", "0", "0", "0", "HTML", "");')
    viewer = _text_response("<html><body>" + ("가" * 500) + "</body></html>")
    http_client.get.side_effect = [main, viewer, viewer]
    client = DartDisclosureClient("secret", http_client=http_client)

    text = await client.fetch_disclosure_text("20260720000120", max_chars=100)

    assert len(text) <= 100


async def test_network_errors_are_retried_once_then_reported_without_the_key():
    import httpx

    http_client = AsyncMock()
    http_client.get.side_effect = httpx.ConnectError("연결 실패")
    client = DartDisclosureClient("secret", http_client=http_client)

    with pytest.raises(DartApiError) as excinfo:
        await client.fetch_disclosures("20260720")

    assert http_client.get.await_count == 2
    assert "secret" not in str(excinfo.value)


async def test_client_opens_its_own_http_session_when_none_is_injected():
    from unittest.mock import patch

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return _response({"status": "013", "message": "no data"})

    with patch("services.dart_disclosure_client.httpx.AsyncClient", _Client):
        page = await DartDisclosureClient("secret").fetch_disclosures("20260720")

    assert page.items == []


async def test_fetch_disclosure_text_opens_its_own_http_session_when_none_is_injected():
    from unittest.mock import patch

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return _text_response("<html>문서 생성 중</html>")

    with patch("services.dart_disclosure_client.httpx.AsyncClient", _Client):
        assert await DartDisclosureClient("secret").fetch_disclosure_text("2026072") == ""


def test_response_decoder_falls_back_to_the_text_attribute_without_bytes():
    response = MagicMock()
    response.content = None
    response.text = "본문"

    assert DartDisclosureClient._decode_response_text(response) == "본문"


def test_response_decoder_honours_a_utf8_meta_charset():
    response = MagicMock()
    response.content = '<meta charset="utf-8">한글 본문'.encode("utf-8")
    response.encoding = None

    assert "한글 본문" in DartDisclosureClient._decode_response_text(response)


def test_response_decoder_normalizes_an_euc_kr_response_encoding():
    response = MagicMock()
    response.content = "한글 본문".encode("cp949")
    response.encoding = "EUC-KR"

    assert DartDisclosureClient._decode_response_text(response) == "한글 본문"


def test_response_decoder_falls_back_to_cp949_when_utf8_fails():
    response = MagicMock()
    response.content = "한글 본문".encode("cp949")
    response.encoding = ""

    assert DartDisclosureClient._decode_response_text(response) == "한글 본문"
