#!/usr/bin/env python3
"""
Minimal diagnostic script for lean-lsp-mcp.

This script attempts to start lean-lsp-mcp and perform a basic
initialize handshake. It is intentionally standalone so it can be
run outside of the full LeanEcon harness.

Usage:
    python scripts/diagnose_lean_lsp_mcp.py

Output is written to /tmp/lean_lsp_mcp_diagnostic.json
"""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

# Configuration
LEAN_PROJECT_PATH = Path(__file__).resolve().parents[1] / "lean_workspace"
READ_TIMEOUT_SECONDS = 15.0
OUTPUT_PATH = Path("/tmp/lean_lsp_mcp_diagnostic.json")


def find_lean_lsp_mcp_command():
    """Return the command to run lean-lsp-mcp, preferring local binary."""
    repo_root = Path(__file__).resolve().parents[1]
    for local_binary in (
        repo_root / ".venv" / "bin" / "lean-lsp-mcp",
        Path.home() / ".local" / "bin" / "lean-lsp-mcp",
    ):
        if local_binary.exists():
            return [str(local_binary)]
    path_binary = shutil.which("lean-lsp-mcp")
    if path_binary:
        return [path_binary]
    uvx_path = shutil.which("uvx")
    if uvx_path:
        return ["uvx", "lean-lsp-mcp"]
    return None


def main():
    result = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "lean_project_path": str(LEAN_PROJECT_PATH),
        "binary_command": None,
        "started": False,
        "initialize_ok": False,
        "error": None,
        "details": {}
    }

    cmd = find_lean_lsp_mcp_command()
    if not cmd:
        result["error"] = "lean-lsp-mcp not found (no local binary and uvx not available)"
        write_result(result)
        return

    result["binary_command"] = cmd

    if not LEAN_PROJECT_PATH.exists():
        result["error"] = f"Lean workspace not found: {LEAN_PROJECT_PATH}"
        write_result(result)
        return

    env = os.environ.copy()
    env.setdefault("LEAN_PROJECT_PATH", str(LEAN_PROJECT_PATH))

    try:
        process = subprocess.Popen(
            [*cmd, "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(LEAN_PROJECT_PATH),
            env=env,
        )
    except Exception as exc:
        result["error"] = f"Failed to start process: {exc}"
        write_result(result)
        return

    result["started"] = True

    # Send initialize request
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "diagnose-script", "version": "0.1"}
            }
        }
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        process.stdin.flush()

        # Read response with timeout
        payload = None
        error = None

        def reader():
            nonlocal payload, error
            try:
                line = process.stdout.readline()
                if not line:
                    return
                decoded = line.decode("utf-8").strip()
                if decoded.lower().startswith("content-length:"):
                    content_length = int(decoded.split(":", 1)[1].strip())
                    while True:
                        header_line = process.stdout.readline()
                        if not header_line:
                            return
                        if not header_line.strip():
                            break
                    body = process.stdout.read(content_length)
                    if body:
                        payload = json.loads(body.decode("utf-8"))
                elif decoded:
                    payload = json.loads(decoded)
            except Exception as exc:
                error = exc

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout=READ_TIMEOUT_SECONDS)

        if thread.is_alive():
            result["error"] = "Timed out waiting for initialize response"
        elif error:
            result["error"] = f"Error reading response: {error}"
        elif payload:
            if "error" in payload:
                result["error"] = f"Initialize error: {payload['error']}"
            else:
                result["initialize_ok"] = True
                result["details"]["server_info"] = payload.get("result", {}).get("serverInfo")
        else:
            result["error"] = "No payload received"

    except Exception as exc:
        result["error"] = f"Exception during initialize: {exc}"
    finally:
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            if stderr:
                result["details"]["stderr_tail"] = stderr[-4000:]
        except Exception:
            pass
        result["details"]["returncode"] = process.poll()

    write_result(result)


def write_result(result: dict):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Diagnostic result written to {OUTPUT_PATH}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
