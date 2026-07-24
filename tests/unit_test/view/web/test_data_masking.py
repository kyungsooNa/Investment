import logging

from core.loggers.sensitive_data_filter import SensitiveDataFilter
from view.web.data_masking import mask_sensitive_data, redact_sensitive_text


def test_mask_sensitive_data_redacts_nested_values_without_mutating_source():
    source = {
        "account_info": {"number": "1234567890"},
        "data": {
            "tot_evlu_amt": "1000000",
            "available_cash": 500000,
            "ord_no": "0000123456",
            "api_key": "top-secret",
            "positions": [{"code": "005930", "qty": 2}],
        },
    }

    masked = mask_sensitive_data(source)

    assert masked["account_info"]["number"] == "******7890"
    assert masked["data"]["tot_evlu_amt"] is None
    assert masked["data"]["available_cash"] is None
    assert masked["data"]["ord_no"] == "******3456"
    assert masked["data"]["api_key"] == "[REDACTED]"
    assert masked["data"]["positions"][0]["code"] == "005930"
    assert source["data"]["tot_evlu_amt"] == "1000000"
    assert source["data"]["api_key"] == "top-secret"


def test_redact_sensitive_text_removes_named_secrets():
    text = 'api_key="abc123" access_token=token-value password: hunter2'

    redacted = redact_sensitive_text(text)

    assert "abc123" not in redacted
    assert "token-value" not in redacted
    assert "hunter2" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_sensitive_data_log_filter_redacts_formatted_arguments():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="access_token=%s",
        args=("token-value",),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert record.getMessage() == "access_token=[REDACTED]"
