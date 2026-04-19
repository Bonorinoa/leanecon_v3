"""Sorry detection and axiom validation."""

from __future__ import annotations

import re

from src.lean.compiler import has_axiom_warnings

AXIOM_LINE_RE = re.compile(r"uses axioms:\s*(.+)", re.IGNORECASE)
AXIOM_NAME_RE = re.compile(r"[A-Za-z0-9_.']+")
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def detect_sorry(lean_code: str) -> bool:
    """Check if source code contains sorry."""

    return "sorry" in lean_code


def validate_axioms(stderr: str) -> dict:
    """Parse Lean compiler output for axiom usage."""

    axioms_used: set[str] = set()
    for line in stderr.splitlines():
        match = AXIOM_LINE_RE.search(line)
        if not match:
            continue
        axioms_used.update(AXIOM_NAME_RE.findall(match.group(1)))

    ordered_axioms = sorted(axioms_used)
    non_standard = has_axiom_warnings(stderr)
    return {
        "standard_only": not non_standard,
        "axioms_used": ordered_axioms,
        "non_standard": non_standard,
    }


def contains_sorry(theorem_code: str) -> bool:
    """Compatibility wrapper for sorry detection."""

    return detect_sorry(theorem_code)


def has_axiom_warning(lean_output: str) -> bool:
    """Detect whether Lean output contains any non-standard axioms."""

    return bool(validate_axioms(lean_output)["non_standard"])
