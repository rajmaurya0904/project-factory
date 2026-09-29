"""Tests for factory.jsonutil: shared JSON extraction from agent output."""

import pytest

from factory.jsonutil import JsonExtractError, extract_json, strip_code_fence


def test_strip_code_fence_removes_json_fence() -> None:
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_code_fence_removes_plain_fence() -> None:
    assert strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_code_fence_noop_without_fence() -> None:
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'


def test_extract_json_object_clean() -> None:
    assert extract_json('{"a": 1}', kind="object") == {"a": 1}


def test_extract_json_array_clean() -> None:
    assert extract_json("[1, 2, 3]", kind="array") == [1, 2, 3]


def test_extract_json_object_from_fenced_prose() -> None:
    text = 'Here you go:\n```json\n{"a": 1}\n```\nDone.'
    assert extract_json(text, kind="object") == {"a": 1}


def test_extract_json_array_from_surrounding_prose() -> None:
    text = "Sure, here are the items:\n[1, 2, 3]\nHope that helps."
    assert extract_json(text, kind="array") == [1, 2, 3]


def test_extract_json_none_raises() -> None:
    with pytest.raises(JsonExtractError, match="no text to parse"):
        extract_json(None)


def test_extract_json_empty_string_raises() -> None:
    with pytest.raises(JsonExtractError, match="no text to parse"):
        extract_json("")


def test_extract_json_no_brackets_raises() -> None:
    with pytest.raises(JsonExtractError, match="could not find"):
        extract_json("no json here at all", kind="object")


def test_extract_json_wrong_top_level_type_raises() -> None:
    with pytest.raises(JsonExtractError, match="was not a object"):
        extract_json("[1, 2, 3]", kind="object")


def test_extract_json_malformed_bracket_content_raises() -> None:
    with pytest.raises(JsonExtractError, match="not valid JSON"):
        extract_json("{not: valid, json}", kind="object")


def test_extract_json_invalid_kind_raises() -> None:
    with pytest.raises(ValueError, match="kind must be"):
        extract_json("{}", kind="banana")
