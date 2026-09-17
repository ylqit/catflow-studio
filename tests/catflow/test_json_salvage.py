from __future__ import annotations

import json

import pytest

from catflow.application.gateways import ProviderGatewayError, StructuredProviderResult
from catflow_worker.ark_job_gateway import _structured_submission
from catflow_worker.ark_responses import parse_response
from catflow_worker.json_salvage import escape_unstructural_quotes, parse_json_object


def _document(text: str) -> dict:
    return {
        "id": "response-salvage",
        "model": "doubao-seed-2-1-pro",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }


# 值内两个未转义英文双引号:第一个后跟正文、第二个后跟正文,均非结构符
_BROKEN = '{"note": "他说"今天下雨"然后笑了", "count": 2}'


def test_valid_json_is_an_identity_transform():
    text = json.dumps({"a": {"b": [1, "含中文引号「你」的值"]}, "c": None}, ensure_ascii=False)
    repaired, count = escape_unstructural_quotes(text)
    assert repaired == text
    assert count == 0


def test_unescaped_quotes_inside_values_are_repaired():
    payload, repairs = parse_json_object(_BROKEN)
    assert payload == {"note": '他说"今天下雨"然后笑了', "count": 2}
    assert repairs == ["escaped_unstructured_quotes:2"]


def test_escaped_and_chinese_quotes_are_untouched():
    text = r'{"a": "他说\"hi\"，又见「你」和“她”"}'
    payload, repairs = parse_json_object(text)
    assert payload["a"] == '他说"hi"，又见「你」和“她”'
    assert repairs == []


def test_key_quotes_are_never_escaped():
    text = '{"outer": {"inner key": "值"}, "list": ["a", "b"]}'
    repaired, count = escape_unstructural_quotes(text)
    assert repaired == text
    assert count == 0


def test_extra_data_is_not_repairable_and_keeps_the_original_error():
    with pytest.raises(json.JSONDecodeError, match="Extra data"):
        parse_json_object('{"a": 1}}')
    _, count = escape_unstructural_quotes('{"a": 1}}')
    assert count == 0


def test_truncated_text_is_not_repairable():
    # 截断走既有 incomplete / 人工补充路径:不补引号、不补括号
    with pytest.raises(json.JSONDecodeError):
        parse_json_object('{"a": "未闭合')
    _, count = escape_unstructural_quotes('{"a": "未闭合')
    assert count == 0


def test_non_object_payload_is_rejected():
    with pytest.raises(ValueError, match="expected JSON object"):
        parse_json_object("[1, 2]")


def test_parse_response_returns_payload_with_repair_audit():
    result = parse_response(_document(_BROKEN))
    assert result["payload"] == {"note": '他说"今天下雨"然后笑了', "count": 2}
    assert result["textRepairs"] == ["escaped_unstructured_quotes:2"]
    assert result["responseId"] == "response-salvage"
    assert result["model"] == "doubao-seed-2-1-pro"


def test_parse_response_strict_path_has_no_repairs_key():
    result = parse_response(_document('{"a": 1}'))
    assert result["payload"] == {"a": 1}
    assert "textRepairs" not in result


def test_parse_response_unrepairable_text_keeps_invalid_structured_output():
    with pytest.raises(ProviderGatewayError) as excinfo:
        parse_response(_document('{"a": 1}}'))
    assert excinfo.value.code == "invalid_structured_output"
    assert "Extra data" in excinfo.value.message
    assert excinfo.value.response_id == "response-salvage"


def _submission(repairs: tuple[str, ...]) -> dict:
    result = StructuredProviderResult(
        payload={"a": 1},
        response_id="r",
        model="m",
        usage={},
        request_hash="h",
        text_repairs=repairs,
    )
    return _structured_submission(result).result


def test_structured_submission_carries_the_audit_only_when_repaired():
    assert _submission(("escaped_unstructured_quotes:2",))["textRepairs"] == [
        "escaped_unstructured_quotes:2"
    ]
    assert "textRepairs" not in _submission(())
