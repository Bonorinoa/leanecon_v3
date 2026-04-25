"""Minimal long-lived MCP stdio client for lean-lsp-mcp."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any

from src.config import CACHE_DIR, LEAN_WORKSPACE, PROJECT_ROOT


class LeanLSPUnavailableError(RuntimeError):
    """Raised when the Lean LSP MCP bridge is unavailable."""


class LeanLSPClient:
    def __init__(
        self, *, lean_project_path: Path = LEAN_WORKSPACE, read_timeout_seconds: float = 15.0
    ) -> None:
        self.lean_project_path = lean_project_path
        self.read_timeout_seconds = read_timeout_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._started = False
        self._unavailable_reason: str | None = None

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._started = False
            self._unavailable_reason = None
        if process is None:
            return
        self._terminate_process(process)

    def lean_goal(self, file_path: Path, *, line: int, column: int | None = None) -> Any:
        arguments: dict[str, Any] = {
            "file_path": self._normalize_file_path(file_path),
            "line": int(line),
        }
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

    def lean_diagnostic_messages(
        self,
        file_path: Path,
        *,
        severity: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> Any:
        arguments: dict[str, Any] = {"file_path": self._normalize_file_path(file_path)}
        if severity is not None:
            arguments["severity"] = severity
        if start_line is not None:
            arguments["start_line"] = int(start_line)
        if end_line is not None:
            arguments["end_line"] = int(end_line)
        return self._call_tool("lean_diagnostic_messages", arguments)

    def lean_leansearch(self, query: str, *, num_results: int = 8) -> Any:
        return self._call_tool(
            "lean_leansearch",
            {"query": query, "num_results": int(num_results)},
        )

    def lean_local_search(self, query: str, *, limit: int = 8) -> Any:
        return self._call_tool(
            "lean_local_search",
            {"query": query, "limit": int(limit), "project_root": str(self.lean_project_path)},
        )

    def lean_file_outline(self, file_path: Path, *, max_declarations: int | None = None) -> Any:
        arguments: dict[str, Any] = {"file_path": self._normalize_file_path(file_path)}
        if max_declarations is not None:
            arguments["max_declarations"] = int(max_declarations)
        return self._call_tool("lean_file_outline", arguments)

    def lean_loogle(self, query: str, *, num_results: int = 8) -> Any:
        return self._call_tool(
            "lean_loogle",
            {"query": query, "num_results": int(num_results)},
        )

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            with self._lock:
                process = self._ensure_started()
                response = self._send_request(
                    process,
                    "tools/call",
                    {"name": name, "arguments": arguments},
                )
        except LeanLSPUnavailableError as exc:
            with self._lock:
                self._process = None
                self._started = False
                self._unavailable_reason = str(exc)
            raise
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
        if self._unavailable_reason is not None and self._process is None:
            raise LeanLSPUnavailableError(self._unavailable_reason)
        if self._process is not None and self._process.poll() is None and self._started:
            return self._process

        env = os.environ.copy()
        env.setdefault("LEAN_PROJECT_PATH", str(self.lean_project_path))
        uv_cache_dir = CACHE_DIR / "uv"
        uv_cache_dir.mkdir(parents=True, exist_ok=True)
        uv_data_dir = CACHE_DIR / "uv-data"
        uv_data_dir.mkdir(parents=True, exist_ok=True)
        env.setdefault("UV_CACHE_DIR", str(uv_cache_dir))
        env.setdefault("XDG_CACHE_HOME", str(CACHE_DIR))
        env.setdefault("XDG_DATA_HOME", str(uv_data_dir))
        local_binary = PROJECT_ROOT / ".venv" / "bin" / "lean-lsp-mcp"
        command = [str(local_binary)] if local_binary.exists() else ["uvx", "lean-lsp-mcp"]
        process = subprocess.Popen(
            [*command, "--transport", "stdio", "--lean-project-path", str(self.lean_project_path)],
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
            self._terminate_process(process)
            self._process = None
            self._started = False
            self._unavailable_reason = f"Failed to initialize lean-lsp-mcp: {exc}"
            raise LeanLSPUnavailableError(self._unavailable_reason) from exc
        self._started = True
        self._unavailable_reason = None
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
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))
        process.stdin.flush()

    def _read_message(self, process: subprocess.Popen[bytes]) -> dict[str, Any]:
        payload: dict[str, Any] | None = None
        error: BaseException | None = None

        def _reader() -> None:
            nonlocal payload, error
            try:
                payload = self._read_message_blocking(process)
            except BaseException as exc:  # pragma: no cover - defensive timeout wrapper
                error = exc

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(timeout=self.read_timeout_seconds)
        if thread.is_alive():
            self._terminate_process(process)
            raise LeanLSPUnavailableError(
                f"Timed out waiting for lean-lsp-mcp after {self.read_timeout_seconds:.0f}s."
            )
        if error is not None:
            raise error
        if payload is None:
            raise LeanLSPUnavailableError("lean-lsp-mcp returned no payload.")
        return payload

    def _read_message_blocking(self, process: subprocess.Popen[bytes]) -> dict[str, Any]:
        assert process.stdout is not None
        while True:
            line = process.stdout.readline()
            if not line:
                raise LeanLSPUnavailableError("lean-lsp-mcp terminated unexpectedly.")
            decoded = line.decode("utf-8").strip()
            if not decoded:
                continue
            return json.loads(decoded)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

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
