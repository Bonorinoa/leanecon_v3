"""REPL-assisted identifier validation for the formalizer.

Uses LeanInteract to validate Lean 4 identifiers and retrieve type signatures
before or after LLM calls. All public functions are guarded by
``FORMALIZER_REPL_VALIDATION_ENABLED`` and wrapped in broad try/except blocks
so that REPL failures never block formalization — callers always get a
graceful fallback (empty dict / None).

Cold-start cost: opening a new ``LeanREPLSession`` starts an ``AutoLeanServer``
process and loads the local workspace via ``LeanREPLConfig``. On a warm build
this typically takes 2–5 seconds. Each subsequent ``run_command`` within the
same session is sub-100 ms.  All sessions here are short-lived (open → validate
→ close), so the cold-start cost is paid once per ``validate_identifiers`` /
``get_type_signature`` call.  Timing telemetry is logged at DEBUG level.
"""

from __future__ import annotations

import logging
import time

from src.config import FORMALIZER_REPL_VALIDATION_ENABLED

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------
# lean_interact (and therefore LeanREPLSession) is an optional runtime dep.
# src/lean/__init__.py exports it as None when the package is absent so that
# the rest of the codebase can import it unconditionally.
try:
    from src.lean import LeanREPLSession as _LeanREPLSession

    _LEAN_INTERACT_AVAILABLE = _LeanREPLSession is not None
except Exception:  # pragma: no cover
    _LeanREPLSession = None
    _LEAN_INTERACT_AVAILABLE = False

try:
    from lean_interact.interface import LeanError as _LeanError
except ImportError:  # pragma: no cover
    _LeanError = type(None)  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_import_block(imports: list[str]) -> str:
    return "\n".join(f"import {imp}" for imp in imports)


def _make_command(import_block: str, body: str) -> str:
    if import_block:
        return f"{import_block}\n\n{body}"
    return body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def validate_identifiers(
    identifiers: list[str],
    imports: list[str],
) -> dict[str, bool]:
    """Check which identifiers are resolvable in the context of *imports*.

    Sends a single ``#check <ident>`` batch command to a short-lived REPL
    session and returns a mapping of ``identifier -> exists``.

    Returns an **empty dict** (not a dict of False values) when:
    - ``FORMALIZER_REPL_VALIDATION_ENABLED`` is False
    - ``lean_interact`` is not installed
    - The REPL fails for any reason

    Callers should treat an empty result as "unknown" and skip filtering.
    """
    if not identifiers:
        return {}
    if not FORMALIZER_REPL_VALIDATION_ENABLED or not _LEAN_INTERACT_AVAILABLE:
        return {}

    import_block = _build_import_block(imports)

    t0 = time.perf_counter()
    try:
        # Check each identifier individually so we can use lean_code_is_valid()
        # for reliable presence detection regardless of exact error message format.
        # All checks share one REPL session (one cold start, N sub-100ms commands).
        result: dict[str, bool] = {}
        with _LeanREPLSession() as repl:  # type: ignore[operator]
            for ident in identifiers:
                command = _make_command(import_block, f"#check {ident}")
                response = repl.run_command(command)
                if isinstance(response, _LeanError):  # type: ignore[arg-type]
                    result[ident] = False
                else:
                    result[ident] = response.lean_code_is_valid()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.debug(
            "REPL validate_identifiers: %.1f ms for %d identifiers", elapsed_ms, len(identifiers)
        )
        return result

    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.warning(
            "REPL validate_identifiers failed after %.1f ms — falling back",
            elapsed_ms,
            exc_info=True,
        )
        return {}


async def get_type_signature(
    identifier: str,
    imports: list[str],
) -> str | None:
    """Return the elaborated type of *identifier*, or ``None`` if unavailable.

    Uses ``#print <identifier>`` in a short-lived REPL session and extracts the
    first info-level message from the response (the printed declaration).

    Returns ``None`` when:
    - ``FORMALIZER_REPL_VALIDATION_ENABLED`` is False
    - ``lean_interact`` is not installed
    - The identifier does not exist
    - The REPL fails for any reason
    """
    if not identifier or not FORMALIZER_REPL_VALIDATION_ENABLED or not _LEAN_INTERACT_AVAILABLE:
        return None

    import_block = _build_import_block(imports)
    command = _make_command(import_block, f"#print {identifier}")

    t0 = time.perf_counter()
    try:
        with _LeanREPLSession() as repl:  # type: ignore[operator]
            response = repl.run_command(command)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.debug(
            "REPL get_type_signature: %.1f ms for '%s'", elapsed_ms, identifier
        )

        if isinstance(response, _LeanError):  # type: ignore[arg-type]
            return None

        if not response.lean_code_is_valid():
            return None

        # #print output appears as info-level messages
        for msg in response.messages:
            severity = getattr(msg, "severity", None)
            data = getattr(msg, "data", "") or ""
            if severity in ("info", None) and data.strip():
                return data.strip()
        return None

    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.warning(
            "REPL get_type_signature failed after %.1f ms for '%s' — falling back",
            elapsed_ms,
            identifier,
            exc_info=True,
        )
        return None
