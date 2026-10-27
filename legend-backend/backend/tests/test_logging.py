import json
import logging

from backend.utils.logging_setup import JsonFormatter


def test_json_formatter_emits_whitelisted_fields_only() -> None:
    record = logging.LogRecord("backend.request", logging.INFO, __file__, 1, "request completed", None, None)
    record.request_id = "abc"
    record.status_code = 200
    record.unexpected_field = "must not appear"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "request completed"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc"
    assert payload["status_code"] == 200
    assert "unexpected_field" not in payload
