"""Lean 4 compilation primitives for LeanEcon v2."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.config import LEAN_TIMEOUT, LEAN_WORKSPACE

AXIOM_LINE_RE = re.compile(r"uses axioms:\s*(.+)", re.IGNORECASE)
AXIOM_NAME_RE = re.compile(r"[A-Za-z0-9_.']+")
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
LEAN_TOOLCHAIN_PROBE_TIMEOUT = 15


def _temp_lean_path() -> Path:
    """Return a unique temporary Lean file path inside the Lean workspace."""

    return LEAN_WORKSPACE / f"_v2_check_{uuid4().hex}.lean"


def lean_workspace_available() -> bool:
    """Return whether the local Lean workspace looks runnable."""

    return lean_workspace_probe()["available"]


def lean_workspace_probe(*, timeout: int = LEAN_TOOLCHAIN_PROBE_TIMEOUT) -> dict[str, Any]:
    """Probe whether the Lean workspace and lake toolchain are actually usable."""

    if not LEAN_WORKSPACE.exists():
        return {"available": False, "reason": "Lean workspace directory is missing."}

    if not (LEAN_WORKSPACE / "lake-manifest.json").exists():
        return {"available": False, "reason": "Lean workspace manifest is missing."}

    if not any((LEAN_WORKSPACE / name).exists() for name in ("lakefile.toml", "lakefile.lean")):
        return {"available": False, "reason": "Lake workspace configuration is missing."}

    lake_path = shutil.which("lake")
    if lake_path is None:
        return {"available": False, "reason": "lake executable not found on PATH."}

    try:
        result = subprocess.run(
            ["lake", "env", "lean", "--version"],
            cwd=str(LEAN_WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "available": False,
            "reason": f"lake env lean --version timed out after {timeout}s.",
            "lake_path": lake_path,
        }
    except FileNotFoundError:
        return {"available": False, "reason": "lake executable not found on PATH."}

    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if not output:
            output = f"lake env lean --version exited with code {result.returncode}."
        return {"available": False, "reason": output, "lake_path": lake_path}

    return {
        "available": True,
        "reason": None,
        "lake_path": lake_path,
        "lean_version": result.stdout.strip() or result.stderr.strip(),
    }


def _relative_to_workspace(path: Path) -> str:
    """Return a stable workspace-relative path for `lake env lean`."""

    return str(path.resolve().relative_to(LEAN_WORKSPACE.resolve()))


def _split_diagnostics(output: str) -> tuple[list[str], list[str]]:
    """Extract plain-text Lean errors and warnings from compiler output."""

    errors: list[str] = []
    warnings: list[str] = []
    pending_level: str | None = None
    pending_lines: list[str] = []

    def flush() -> None:
        nonlocal pending_level, pending_lines
        if pending_level and pending_lines:
            payload = "\n".join(pending_lines).strip()
            if payload:
                if pending_level == "error":
                    errors.append(payload)
                else:
                    warnings.append(payload)
        pending_level = None
        pending_lines = []

    for line in output.splitlines():
        lowered = line.lower()
        if "error:" in lowered:
            flush()
            pending_level = "error"
            pending_lines = [line]
            continue
        if "warning:" in lowered:
            flush()
            pending_level = "warning"
            pending_lines = [line]
            continue
        if pending_level and (line.startswith(" ") or line.startswith("\t")):
            pending_lines.append(line)
            continue
        if pending_level:
            flush()

    flush()
    return errors, warnings


def lean_run_code(
    lean_code: str,
    *,
    timeout: int = LEAN_TIMEOUT,
    filename: str | None = None,
) -> dict:
    """Compile a standalone Lean snippet using `lake env lean`."""

    if filename:
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", Path(filename).stem).strip("_") or "v2_check"
        temp_path = LEAN_WORKSPACE / f"{stem}_{uuid4().hex[:10]}.lean"
    else:
        temp_path = _temp_lean_path()
    temp_path.write_text(lean_code, encoding="utf-8")

    try:
        try:
            result = subprocess.run(
                ["lake", "env", "lean", _relative_to_workspace(temp_path)],
                cwd=str(LEAN_WORKSPACE),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"lake env lean timed out after {timeout}s",
                "exit_code": -1,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "stdout": "",
                "stderr": "lake executable not found on PATH",
                "exit_code": -1,
            }

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    finally:
        temp_path.unlink(missing_ok=True)


def sorry_in_output(output: str) -> bool:
    """Check if Lean output contains sorry warnings."""

    lowered = output.lower()
    return "declaration uses `sorry`" in lowered or "declaration uses 'sorry'" in lowered


def has_axiom_warnings(output: str) -> list[str]:
    """Extract non-standard axiom usage from Lean output."""

    axiom_names: set[str] = set()
    for line in output.splitlines():
        match = AXIOM_LINE_RE.search(line)
        if not match:
            continue
        axiom_names.update(AXIOM_NAME_RE.findall(match.group(1)))

    return sorted(name for name in axiom_names if name not in STANDARD_AXIOMS)


def compile_check(
    lean_code: str,
    *,
    timeout: int = LEAN_TIMEOUT,
    filename: str | None = None,
    check_axioms: bool = False,
) -> dict:
    """Full compilation check. Returns structured result for /api/v2/compile."""

    _ = check_axioms
    result = lean_run_code(lean_code, timeout=timeout, filename=filename)
    compiler_output = "\n".join(part for part in (result["stdout"], result["stderr"]) if part)
    errors, warnings = _split_diagnostics(compiler_output)
    has_sorry = sorry_in_output(compiler_output)
    axiom_warnings = has_axiom_warnings(compiler_output)
    combined_output = "\n".join(
        part for part in (result["stdout"], result["stderr"]) if part
    ).strip()

    if not result["success"] and not errors:
        fallback_error = "\n".join(
            part for part in (result["stderr"], result["stdout"]) if part
        ).strip()
        if fallback_error:
            errors.append(fallback_error)

    if has_sorry and "Proof contains 'sorry'." not in warnings:
        warnings.append("Proof contains 'sorry'.")

    return {
        "success": result["success"] and not has_sorry,
        "has_sorry": has_sorry,
        "axiom_warnings": axiom_warnings,
        "output": combined_output,
        "errors": errors if not result["success"] else [],
        "warnings": warnings,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
    }


def compile_lean_code(
    lean_code: str,
    *,
    timeout: int = LEAN_TIMEOUT,
    filename: str | None = None,
    check_axioms: bool = False,
) -> dict:
    """Compatibility wrapper for direct Lean compilation."""

    return compile_check(
        lean_code,
        timeout=timeout,
        filename=filename,
        check_axioms=check_axioms,
    )
