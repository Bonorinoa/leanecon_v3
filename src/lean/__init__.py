"""Lean integration exports."""

from .compiler import (
    compile_check,
    compile_lean_code,
    is_transient_lake_failure,
    lean_run_code,
    lean_workspace_available,
    lean_workspace_probe,
    lean_workspace_warm,
)
from .error_classifier import (
    ClassifiedError,
    classify_lean_errors,
    suggested_import_for_identifier,
    suggested_preamble_entry_for_identifier,
)
from .validators import contains_sorry, detect_sorry, has_axiom_warning, validate_axioms

try:
    from .repl import LeanREPL, LeanREPLSession, ProofSessionState, TacticResult
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    LeanREPL = None  # type: ignore[assignment]
    LeanREPLSession = None  # type: ignore[assignment]
    ProofSessionState = None  # type: ignore[assignment]
    TacticResult = None  # type: ignore[assignment]

__all__ = [
    "compile_check",
    "compile_lean_code",
    "is_transient_lake_failure",
    "lean_run_code",
    "lean_workspace_available",
    "lean_workspace_probe",
    "lean_workspace_warm",
    "ClassifiedError",
    "classify_lean_errors",
    "suggested_import_for_identifier",
    "suggested_preamble_entry_for_identifier",
    "contains_sorry",
    "detect_sorry",
    "has_axiom_warning",
    "validate_axioms",
    "LeanREPL",
    "LeanREPLSession",
    "ProofSessionState",
    "TacticResult",
]
