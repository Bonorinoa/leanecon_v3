"""Minimal long-lived MCP stdio client for lean-lsp-mcp."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any

from src.config import LEAN_WORKSPACE, PROJECT_ROOT


class LeanLSPUnavailableError(RuntimeError):
    """Raised when the Lean LSP MCP bridge is unavailable."""


class LeanLSPClient:
    def __init__(self, *, lean_project_path: Path = LEAN_WORKSPACE) -> None:
        self.lean_project_path = lean_project_path
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._started = False

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._started = False
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def lean_goal(self, file_path: Path, *, line: int, column: int | None = None) -> Any:
        arguments: dict[str, Any] = {"file_path": self._normalize_file_path(file_path), "line": int(line)}
        if column is not None:
            arguments["column"] = int(column)
        return self._call_tool("lean_goal", arguments)

    def lean_code_actions(self, file_path: Path, *, line: int) -> Any:
        return self._call_tool(
            "lean_code_actions",
            {"file_path": self._normalize_file_path(file_path), "line": int(line)},
        )

    def lean_hover_info(self, file_path: Path, *, line: int, column: int) -> Any:
        return self._call_tool(
            "lean_hover_info",
            {
                "file_path": self._normalize_file_path(file_path),
                "line": int(line),
                "column": int(column),
            },
        )

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            process = self._ensure_started()
            response = self._send_request(
                process,
                "tools/call",
                {"name": name, "arguments": arguments},
            )
        if "error" in response:
            message = response["error"].get("message", "Lean LSP MCP tool call failed.")
            raise LeanLSPUnavailableError(str(message))
        result = response.get("result", {})
        if result.get("isError"):
            text = self._extract_text(result)
            raise LeanLSPUnavailableError(text or f"{name} failed.")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        text = self._extract_text(result)
        if text is not None:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return result

    def _ensure_started(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None and self._started:
            return self._process

        env = os.environ.copy()
        env.setdefault("LEAN_PROJECT_PATH", str(self.lean_project_path))
        process = subprocess.Popen(
            ["uvx", "lean-lsp-mcp", "--transport", "stdio", "--lean-project-path", str(self.lean_project_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        if process.stdin is None or process.stdout is None:
            raise LeanLSPUnavailableError("lean-lsp-mcp stdio pipes are unavailable.")
        self._process = process
        try:
            self._send_request(
                process,
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "leanecon-v3", "version": "3.0.0-alpha"},
                },
            )
            self._send_notification(process, "notifications/initialized", {})
        except Exception as exc:
            self.close()
            raise LeanLSPUnavailableError(f"Failed to initialize lean-lsp-mcp: {exc}") from exc
        self._started = True
        return process

    def _send_notification(
        self,
        process: subprocess.Popen[bytes],
        method: str,
        params: dict[str, Any],
    ) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write_message(process, message)

    def _send_request(
        self,
        process: subprocess.Popen[bytes],
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._request_id += 1
        message = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        self._write_message(process, message)
        while True:
            payload = self._read_message(process)
            if payload.get("id") == self._request_id:
                return payload

    def _write_message(self, process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        data = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        assert process.stdin is not None
        process.stdin.write(data)
        process.stdin.flush()

    def _read_message(self, process: subprocess.Popen[bytes]) -> dict[str, Any]:
        assert process.stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = process.stdout.readline()
            if not line:
                raise LeanLSPUnavailableError("lean-lsp-mcp terminated unexpectedly.")
            if line in {b"\r\n", b"\n"}:
                break
            header = line.decode("utf-8").strip()
            if ":" in header:
                key, value = header.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        if "content-length" not in headers:
            raise LeanLSPUnavailableError("Missing Content-Length in lean-lsp-mcp response.")
        length = int(headers["content-length"])
        body = process.stdout.read(length)
        if not body:
            raise LeanLSPUnavailableError("Empty lean-lsp-mcp response body.")
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str | None:
        content = result.get("content")
        if isinstance(content, list):
            text_chunks = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            combined = "\n".join(chunk for chunk in text_chunks if chunk)
            return combined or None
        if isinstance(content, str):
            return content
        return None

    def _normalize_file_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(self.lean_project_path))
        except ValueError:
            return str(resolved)


default_lean_lsp_client = LeanLSPClient()
