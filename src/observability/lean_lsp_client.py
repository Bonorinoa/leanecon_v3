"""Minimal long-lived MCP stdio client for lean-lsp-mcp."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

from src.config import CACHE_DIR, LEAN_WORKSPACE, PROJECT_ROOT


class LeanLSPUnavailableError(RuntimeError):
    """Raised when the Lean LSP MCP bridge is unavailable."""


class LeanLSPToolError(LeanLSPUnavailableError):
    """Raised when a specific MCP tool reports an execution error."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(message)
        self.tool_name = tool_name


class LeanLSPClient:
    def __init__(
        self, *, lean_project_path: Path = LEAN_WORKSPACE, read_timeout_seconds: float | None = None
    ) -> None:
        self.lean_project_path = lean_project_path
        if read_timeout_seconds is None:
            read_timeout_seconds = float(os.environ.get("LEANECON_LSP_READ_TIMEOUT", "45.0"))
        self.read_timeout_seconds = read_timeout_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._started = False
        self._unavailable_reason: str | None = None

    def _emit_lsp_event(self, event_type: str, **kwargs: Any) -> None:
        """Emit a lightweight structured event for LSP state changes."""
        try:
            from src.observability import log_event

            log_event(event_type, {"lsp_client": True, **kwargs})
        except Exception:
            pass  # Observability failures must never break the prover

    def status(self) -> dict[str, Any]:
        """Return readiness without starting ``lean-lsp-mcp``."""
        command, binary_label = self._resolve_command()
        binary_available = command is not None
        workspace_available = self.lean_project_path.exists()
        process_running = self._process is not None and self._process.poll() is None
        if self._unavailable_reason is not None and not process_running:
            state = "unavailable"
            reason = self._unavailable_reason
        elif process_running and self._started:
            state = "ready"
            reason = None
        elif not workspace_available:
            state = "unavailable"
            reason = f"Lean workspace not found: {self.lean_project_path}"
        elif not binary_available:
            state = "unavailable"
            reason = "lean-lsp-mcp is not installed and uvx is not available."
        else:
            state = "ready"
            reason = None
        return {
            "name": "lean_lsp",
            "state": state,
            "binary": binary_label,
            "binary_available": binary_available,
            "workspace_available": workspace_available,
            "available": state == "ready",
            "benchmark_ready": state == "ready",
            "reason": reason,
            "process_running": process_running,
        }

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._started = False
            self._unavailable_reason = None
        if process is None:
            return
        self._terminate_process(process)

    def lean_goal(self, file_path: Path | str, *, line: int, column: int | None = None) -> Any:
        arguments: dict[str, Any] = {
            "file_path": self._normalize_file_path(file_path),
            "line": int(line),
        }
        if column is not None:
            arguments["column"] = int(column)
        return self._call_tool("lean_goal", arguments)

    def lean_code_actions(self, file_path: Path | str, *, line: int) -> Any:
        return self._call_tool(
            "lean_code_actions",
            {"file_path": self._normalize_file_path(file_path), "line": int(line)},
        )

    def lean_hover_info(self, file_path: Path | str, *, line: int, column: int) -> Any:
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
        file_path: Path | str,
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

    def lean_file_outline(
        self, file_path: Path | str, *, max_declarations: int | None = None
    ) -> Any:
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
        response: dict[str, Any] | None = None
        last_error: LeanLSPUnavailableError | None = None
        for attempt in range(2):
            try:
                with self._lock:
                    process = self._ensure_started()
                    response = self._send_request(
                        process,
                        "tools/call",
                        {"name": name, "arguments": arguments},
                    )
                break
            except LeanLSPUnavailableError as exc:
                last_error = exc
                with self._lock:
                    can_retry = isinstance(self._process, subprocess.Popen)
                    self._reset_process(str(exc))
                self._emit_lsp_event(
                    "lsp_client_tool_transport_error",
                    tool_name=name,
                    attempt=attempt + 1,
                    reason=str(exc),
                )
                if attempt == 0 and can_retry and self._should_retry_tool_transport(exc):
                    time.sleep(self._cold_start_grace_seconds())
                    continue
                raise
        if response is None:
            raise last_error or LeanLSPUnavailableError("Lean LSP MCP tool call failed.")
        if "error" in response:
            message = response["error"].get("message", "Lean LSP MCP tool call failed.")
            raise LeanLSPToolError(name, str(message))
        result = response.get("result", {})
        if result.get("isError"):
            text = self._extract_text(result)
            raise LeanLSPToolError(name, text or f"{name} failed.")
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

        last_error: Exception | None = None
        for attempt in range(2):
            if attempt > 0:
                time.sleep(self._cold_start_grace_seconds())
            try:
                process = self._start_process()
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
                last_error = exc
                self._terminate_process(process) if "process" in locals() else None
                self._process = None
                self._started = False
                self._unavailable_reason = (
                    f"Failed to initialize lean-lsp-mcp (attempt {attempt + 1}): {exc}"
                )
                self._emit_lsp_event(
                    "lsp_client_unavailable",
                    reason=self._unavailable_reason,
                    attempt=attempt + 1,
                )
                continue
            self._process = process
            self._started = True
            self._unavailable_reason = None
            self._emit_lsp_event(
                "lsp_client_recovered" if attempt > 0 else "lsp_client_started"
            )
            return process

        reason = self._unavailable_reason or f"Failed to initialize lean-lsp-mcp: {last_error}"
        raise LeanLSPUnavailableError(reason)

    def _start_process(self) -> subprocess.Popen[bytes]:
        command, _binary_label = self._resolve_command()
        if command is None:
            raise LeanLSPUnavailableError("lean-lsp-mcp is not installed and uvx is not available.")

        env = os.environ.copy()
        env.setdefault("LEAN_PROJECT_PATH", str(self.lean_project_path))
        uv_cache_dir = CACHE_DIR / "uv"
        uv_cache_dir.mkdir(parents=True, exist_ok=True)
        uv_data_dir = CACHE_DIR / "uv-data"
        uv_data_dir.mkdir(parents=True, exist_ok=True)
        env.setdefault("UV_CACHE_DIR", str(uv_cache_dir))
        env.setdefault("XDG_CACHE_HOME", str(CACHE_DIR))
        env.setdefault("XDG_DATA_HOME", str(uv_data_dir))
        process = subprocess.Popen(
            [*command, "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.lean_project_path),
            env=env,
        )
        if process.stdin is None or process.stdout is None:
            raise LeanLSPUnavailableError("lean-lsp-mcp stdio pipes are unavailable.")
        return process

    def _reset_process(self, reason: str) -> None:
        process = self._process
        self._process = None
        self._started = False
        self._unavailable_reason = reason
        if process is not None:
            self._terminate_process(process)

    def _resolve_command(self) -> tuple[list[str] | None, str | None]:
        candidates = (
            PROJECT_ROOT / ".venv" / "bin" / "lean-lsp-mcp",
            Path.home() / ".local" / "bin" / "lean-lsp-mcp",
        )
        for candidate in candidates:
            if candidate.exists():
                return [str(candidate)], str(candidate)
        path_binary = shutil.which("lean-lsp-mcp")
        if path_binary:
            return [path_binary], path_binary
        if shutil.which("uvx"):
            return ["uvx", "lean-lsp-mcp"], "uvx lean-lsp-mcp"
        return None, None

    @staticmethod
    def _cold_start_grace_seconds() -> float:
        return float(os.environ.get("LEANECON_LSP_COLD_START_GRACE_SECONDS", "2.0"))

    @staticmethod
    def _should_retry_tool_transport(exc: LeanLSPUnavailableError) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "terminated unexpectedly",
                "timed out waiting",
                "returned no payload",
                "stdio pipes",
                "broken pipe",
            )
        )

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
            if decoded.lower().startswith("content-length:"):
                try:
                    content_length = int(decoded.split(":", 1)[1].strip())
                except ValueError as exc:
                    raise LeanLSPUnavailableError(
                        f"Invalid lean-lsp-mcp content length header: {decoded}"
                    ) from exc
                while True:
                    header_line = process.stdout.readline()
                    if not header_line:
                        raise LeanLSPUnavailableError("lean-lsp-mcp terminated unexpectedly.")
                    if not header_line.strip():
                        break
                body = process.stdout.read(content_length)
                if not body:
                    raise LeanLSPUnavailableError("lean-lsp-mcp returned no payload.")
                return json.loads(body.decode("utf-8"))
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

    def _normalize_file_path(self, path: Path | str) -> str:
        if isinstance(path, str):
            raw = path.strip()
            if not raw:
                return raw
            if raw.endswith(".lean"):
                if not Path(raw).is_absolute():
                    relative_path = raw.replace("\\", "/")
                    package_path = self._resolve_lake_package_file(relative_path)
                    if package_path is not None:
                        return str(package_path)
                    return relative_path
                path = Path(raw)
            elif "/" not in raw and "\\" not in raw and "." in raw:
                relative_path = raw.replace(".", "/") + ".lean"
                package_path = self._resolve_lake_package_file(relative_path)
                if package_path is not None:
                    return str(package_path)
                return relative_path
            else:
                path = Path(raw)

        resolved = path.resolve()
        try:
            return str(resolved.relative_to(self.lean_project_path))
        except ValueError:
            return str(resolved)

    def _resolve_lake_package_file(self, relative_path: str) -> Path | None:
        package_root = self.lean_project_path / ".lake" / "packages"
        if not package_root.exists():
            return None
        try:
            packages = list(package_root.iterdir())
        except OSError:
            return None
        for package_dir in packages:
            candidate = package_dir / relative_path
            if candidate.exists():
                return candidate.resolve()
        return None


class NullLeanLSPClient:
    """Explicit disabled Lean LSP client for deterministic unit/CI paths."""

    def __init__(self, *, reason: str = "Lean LSP is disabled.") -> None:
        self.reason = reason

    def status(self) -> dict[str, Any]:
        return {
            "name": "lean_lsp",
            "state": "disabled",
            "binary": None,
            "binary_available": False,
            "workspace_available": LEAN_WORKSPACE.exists(),
            "available": False,
            "benchmark_ready": False,
            "reason": self.reason,
            "process_running": False,
        }

    def close(self) -> None:
        return None

    def _raise(self, *_args: Any, **_kwargs: Any) -> Any:
        raise LeanLSPUnavailableError(self.reason)

    lean_goal = _raise
    lean_code_actions = _raise
    lean_hover_info = _raise
    lean_diagnostic_messages = _raise
    lean_leansearch = _raise
    lean_local_search = _raise
    lean_file_outline = _raise
    lean_loogle = _raise


_shared_lean_lsp_client: LeanLSPClient | None = None


def build_default_lean_lsp_client() -> LeanLSPClient | NullLeanLSPClient:
    global _shared_lean_lsp_client
    mode = os.getenv("LEANECON_LEAN_LSP_MODE", "auto").strip().lower()
    if mode in {"disabled", "off", "0", "false", "none"}:
        return NullLeanLSPClient(
            reason="Lean LSP disabled by LEANECON_LEAN_LSP_MODE."
        )
    if _shared_lean_lsp_client is None:
        _shared_lean_lsp_client = LeanLSPClient()
    return _shared_lean_lsp_client


default_lean_lsp_client = build_default_lean_lsp_client()
