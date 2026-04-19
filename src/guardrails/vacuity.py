"""Vacuity rejection helpers."""

from __future__ import annotations

import re

VACUOUS_PATTERNS = [
    re.compile(r"\(\s*\w+\s*:\s*Prop\s*\)\s*:\s*[\s\S]*?:=", re.IGNORECASE),
    re.compile(r"\(\s*h\s*:\s*\w+\s*\)\s*:\s*\w+\s*:=\s*h\b", re.IGNORECASE),
    re.compile(r":\s*True\s*:=", re.IGNORECASE),
    re.compile(r"theorem\s+\w+\s*:\s*[A-Z]\w*\s*:=\s*by", re.IGNORECASE),
]


def is_vacuous(theorem_code: str) -> bool:
    normalized = theorem_code.strip()
    if not normalized:
        return True
    if "True := by" in normalized:
        return True
    return any(pattern.search(normalized) for pattern in VACUOUS_PATTERNS)


def vacuity_report(theorem_code: str) -> dict[str, object]:
    return {
        "is_vacuous": is_vacuous(theorem_code),
        "reason": "matches_vacuity_pattern" if is_vacuous(theorem_code) else "not_vacuous",
    }
