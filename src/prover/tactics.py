"""Tactic heuristics and tool guardrails for the prover."""

from __future__ import annotations

import re
from typing import Any

from src.prover.models import ProverAction, ProverTraceStep
from src.tools import ToolRegistry

ALLOWED_TOOL_NAMES = frozenset(
    {
        "lean_run_code",
        "compile_current_code",
        "read_current_code",
        "write_current_code",
        "get_goals",
        "apply_tactic",
        "lean_goal",
        "lean_code_actions",
        "lean_hover_info",
        "memory_retrieve",
    }
)


def direct_hypothesis_name(theorem_code: str) -> str | None:
    # Locate the theorem signature up to `:= by` (handles both multi-line stubs
    # and single-line formalizer output).
    sig_match = re.search(
        r"(?:theorem|lemma)\s+[A-Za-z0-9_']+(.*?):=\s*by",
        theorem_code,
        re.DOTALL,
    )
    if sig_match is None:
        return None
    signature = sig_match.group(1)
    depth = 0
    last_colon = -1
    i = 0
    while i < len(signature):
        ch = signature[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            # Skip `:=` that we might have captured partially.
            if i + 1 < len(signature) and signature[i + 1] == "=":
                i += 2
                continue
            last_colon = i
        i += 1
    if last_colon == -1:
        return None
    goal = " ".join(signature[last_colon + 1 :].split())
    binders = signature[:last_colon]
    for match in re.finditer(r"\((?P<name>[A-Za-z0-9_']+)\s*:\s*(?P<body>[^)]*)\)", binders):
        body = " ".join(match.group("body").split())
        if body == goal:
            return match.group("name")
    return None


def suggest_fast_path_tactics(theorem_code: str) -> list[str]:
    tactics: list[str] = []
    direct = direct_hypothesis_name(theorem_code)
    if direct:
        tactics.extend([f"exact {direct}", f"simpa using {direct}"])
    normalized = theorem_code.lower()
    if "nkpc" in normalized:
        tactics.extend(["simpa [nkpc]", "ring"])
    if any(token in normalized for token in ("field", "div", "/")):
        tactics.extend(["field_simp", "ring"])
    if "ring" in normalized or any(token in normalized for token in ("+", "-", "*", "^")):
        tactics.append("ring")
    tactics.extend(["simp", "aesop", "norm_num"])

    deduped: list[str] = []
    for tactic in tactics:
        if tactic not in deduped:
            deduped.append(tactic)
    return deduped


def validate_action(action: ProverAction, registry: ToolRegistry) -> str | None:
    if action.action_type == "finish":
        return None
    if action.action_type == "decompose":
        if not action.decomposition_statement:
            return "Decomposition requires a theorem statement."
        return None
    if action.tool is None:
        return "Tool actions must include a tool invocation."
    if action.tool.name not in ALLOWED_TOOL_NAMES:
        return f"Tool `{action.tool.name}` is not allowed."
    if registry.get(action.tool.name) is None:
        return f"Tool `{action.tool.name}` is not registered."
    if action.tool.name == "write_current_code":
        if "path" in action.tool.arguments or "filename" in action.tool.arguments:
            return "write_current_code may only update the active theorem code."
        code = action.tool.arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            return "write_current_code requires non-empty `code`."
    if action.tool.name == "apply_tactic":
        tactic = action.tool.arguments.get("tactic")
        if not isinstance(tactic, str) or not tactic.strip():
            return "apply_tactic requires non-empty `tactic`."
    return None


def repeated_noop_action(trace: list[ProverTraceStep], action: ProverAction) -> bool:
    if action.action_type != "tool" or action.tool is None:
        return False
    recent = trace[-2:]
    if len(recent) < 2:
        return False
    for step in recent:
        if step.success:
            return False
        if step.tool_name != action.tool.name:
            return False
        if step.tool_arguments != action.tool.arguments:
            return False
    return True


def should_decompose(
    *,
    failed_turns_for_target: int,
    action: ProverAction | None = None,
    allow_decomposition: bool,
    current_depth: int,
    total_extracted: int,
    no_progress_streak: int = 1,
    direct_candidates_available: bool = False,
    max_recursion_depth: int = 3,
) -> bool:
    if not allow_decomposition:
        return False
    if current_depth >= max_recursion_depth or total_extracted >= 3:
        return False
    if action is not None and action.action_type == "decompose":
        return True
    if direct_candidates_available and no_progress_streak < 3:
        return False
    required_failed_turns = 3 if direct_candidates_available else 2
    if failed_turns_for_target < required_failed_turns or no_progress_streak < 1:
        return False
    return True


def summarize_lesson(
    *,
    outcome: str,
    tactic_sequence: list[str],
    preamble_names: list[str],
    termination_reason: str,
) -> str:
    if outcome == "verified":
        if tactic_sequence:
            return (
                f"Verified with {', '.join(tactic_sequence[:3])}; "
                f"grounded in {', '.join(preamble_names[:2]) or 'local proof context'}."
            )
        return "Verified after iterative Lean feedback repair."
    if termination_reason == "decomposition_limit_reached":
        return "Proof failed after repeated decomposition attempts; simplify the target before proving."
    if tactic_sequence:
        return f"Failed after trying {', '.join(tactic_sequence[:3])}; inspect Lean feedback before retrying."
    return "Failed before a stable tactic sequence emerged; inspect provider output and Lean diagnostics."


def failure_feedback_messages(result: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for key in ("errors", "warnings"):
        for entry in result.get(key) or []:
            text = str(entry).strip()
            if text:
                messages.append(text)
    stderr = str(result.get("stderr") or "").strip()
    if stderr:
        messages.append(stderr)
    return messages
