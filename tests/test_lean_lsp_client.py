"""Stubbed-subprocess tests for the Lean LSP MCP client.

These tests inject a fake JSON-RPC stream so the client can be exercised
end-to-end without spawning the real ``lean-lsp-mcp`` binary. They cover the
parts of the client most likely to break silently in production: response
shape parsing, error escalation, and path normalization.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from src.observability.lean_lsp_client import LeanLSPClient, LeanLSPUnavailableError


class _FakeProcess:
    """Minimal ``subprocess.Popen``-like object backed by in-memory queues."""

    def __init__(self, replies: list[dict[str, Any]]):
        self.stdin = io.BytesIO()
        self._reply_lines = [
            (json.dumps(reply, ensure_ascii=True) + "\n").encode("utf-8")
            for reply in replies
        ]
        self.stdout = self  # readline below
        self.stderr = io.BytesIO()
        self._terminated = False
        self._killed = False

    # subprocess.Popen.poll
    def poll(self):
        return None if not self._terminated else 0

    # stdout.readline (we set self.stdout = self for simplicity)
    def readline(self) -> bytes:
        if not self._reply_lines:
            return b""
        return self._reply_lines.pop(0)

    def terminate(self) -> None:
        self._terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self._killed = True


def _client_with_fake(replies: list[dict[str, Any]]) -> tuple[LeanLSPClient, _FakeProcess]:
    """Return a client whose process is pre-populated and marked started."""
    client = LeanLSPClient(lean_project_path=Path("/tmp/lean_workspace"))
    process = _FakeProcess(replies)
    client._process = process  # type: ignore[assignment]
    client._started = True
    client._unavailable_reason = None
    return client, process


def test_call_tool_returns_structured_content():
    client, _ = _client_with_fake(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"structuredContent": {"items": [{"name": "Foo.bar"}]}},
            }
        ]
    )
    result = client.lean_leansearch("query", num_results=1)
    assert result == {"items": [{"name": "Foo.bar"}]}


def test_call_tool_falls_back_to_text_content_with_json():
    client, _ = _client_with_fake(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {"type": "text", "text": '{"items": [{"name": "X"}]}'}
                    ]
                },
            }
        ]
    )
    result = client.lean_leansearch("q")
    assert result == {"items": [{"name": "X"}]}


def test_call_tool_returns_raw_text_when_not_json():
    client, _ = _client_with_fake(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "plain text"}]},
            }
        ]
    )
    assert client.lean_leansearch("q") == "plain text"


def test_call_tool_raises_on_jsonrpc_error():
    client, _ = _client_with_fake(
        [{"jsonrpc": "2.0", "id": 1, "error": {"message": "rate limited"}}]
    )
    with pytest.raises(LeanLSPUnavailableError, match="rate limited"):
        client.lean_leansearch("q")


def test_call_tool_raises_on_is_error_flag():
    client, _ = _client_with_fake(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "tool blew up"}],
                },
            }
        ]
    )
    with pytest.raises(LeanLSPUnavailableError, match="tool blew up"):
        client.lean_leansearch("q")


def test_call_tool_documents_jsonrpc_error_does_not_reset_state():
    """Document current behavior: a JSON-RPC ``error`` response raises but
    does NOT mark the client unstarted, because the reset path only runs when
    an exception escapes ``_ensure_started`` / ``_send_request``. A future
    refactor (Sprint 25+) should unify these reset paths so any
    LeanLSPUnavailableError, however raised, leaves the client in a clean
    "needs restart" state."""
    client, _ = _client_with_fake(
        [{"jsonrpc": "2.0", "id": 1, "error": {"message": "boom"}}]
    )

    with pytest.raises(LeanLSPUnavailableError, match="boom"):
        client.lean_leansearch("q")

    # CURRENT behavior — note: started flag and process pointer are NOT reset
    # on a JSON-RPC error, only on transport-level exceptions.
    assert client._started is True
    assert client._process is not None


def test_terminated_subprocess_surfaces_as_unavailable():
    client, process = _client_with_fake([])  # no replies → readline returns b""
    with pytest.raises(LeanLSPUnavailableError, match="terminated unexpectedly"):
        client.lean_leansearch("q")


def test_normalize_file_path_relative_to_workspace(tmp_path):
    client = LeanLSPClient(lean_project_path=tmp_path)
    nested = tmp_path / "Mathlib" / "Topology" / "Basic.lean"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("-- placeholder", encoding="utf-8")
    assert client._normalize_file_path(nested) == "Mathlib/Topology/Basic.lean"


def test_normalize_file_path_outside_workspace_returns_absolute(tmp_path):
    client = LeanLSPClient(lean_project_path=tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    outside = tmp_path / "other.lean"
    outside.write_text("-- placeholder", encoding="utf-8")
    # Outside the workspace → falls back to the absolute path.
    assert client._normalize_file_path(outside) == str(outside.resolve())


def test_extract_text_combines_text_chunks():
    payload = {
        "content": [
            {"type": "text", "text": "alpha"},
            {"type": "text", "text": "beta"},
            {"type": "image"},  # ignored
        ]
    }
    assert LeanLSPClient._extract_text(payload) == "alpha\nbeta"


def test_extract_text_returns_none_when_no_textual_content():
    assert LeanLSPClient._extract_text({"content": []}) is None
    assert LeanLSPClient._extract_text({}) is None
