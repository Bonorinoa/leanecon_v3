"""Shared JSON-extraction helper for LLM driver responses.

Each driver (planner, formalizer, prover) used to carry its own near-identical
copy of this routine. They are now consolidated here so that any future tweak
(more robust fence handling, NaN handling, etc.) lands once.
"""

from __future__ import annotations

import json
from typing import Callable


def extract_json_object(
    raw_text: str,
    *,
    error_factory: Callable[[str], Exception],
) -> dict[str, object]:
    """Parse the first JSON object embedded in *raw_text*.

    - Strips an outer ```/```json/```fence``` markdown wrapper if present
      (idempotent if no fence exists).
    - Locates the outermost ``{ ... }`` span and ``json.loads`` it.
    - Raises ``error_factory(message)`` on any failure (missing braces, invalid
      JSON, non-object payload). Callers pass their stage-specific exception.
    """
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise error_factory("Driver did not return a JSON object.")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise error_factory(f"Driver returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise error_factory("Driver returned non-object JSON.")
    return payload
