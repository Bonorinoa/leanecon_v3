"""Tests for the consolidated JSON extraction helper."""

from __future__ import annotations

import pytest

from src.utils.json_extraction import extract_json_object


class _StubError(RuntimeError):
    pass


def _factory(message: str) -> _StubError:
    return _StubError(message)


def test_extract_json_object_plain():
    assert extract_json_object('{"a": 1}', error_factory=_factory) == {"a": 1}


def test_extract_json_object_strips_markdown_fence():
    raw = '```json\n{"a": 1, "b": "x"}\n```'
    assert extract_json_object(raw, error_factory=_factory) == {"a": 1, "b": "x"}


def test_extract_json_object_strips_bare_fence():
    raw = '```\n{"a": 1}\n```'
    assert extract_json_object(raw, error_factory=_factory) == {"a": 1}


def test_extract_json_object_handles_trailing_text():
    raw = 'Here is the JSON:\n{"verdict": "ok"}\n— end.'
    assert extract_json_object(raw, error_factory=_factory) == {"verdict": "ok"}


def test_extract_json_object_no_object_raises():
    with pytest.raises(_StubError, match="did not return a JSON object"):
        extract_json_object("no braces here", error_factory=_factory)


def test_extract_json_object_invalid_json_raises():
    with pytest.raises(_StubError, match="invalid JSON"):
        extract_json_object('{"a": 1,,}', error_factory=_factory)


def test_extract_json_object_idempotent_no_fence():
    raw = '{"a": [1, 2, 3]}'
    assert extract_json_object(raw, error_factory=_factory) == {"a": [1, 2, 3]}
