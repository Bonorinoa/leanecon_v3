"""Tactic heuristics and tool guardrails for the prover."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

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


@dataclass(frozen=True)
class GoalShape:
    kind: Literal["intro", "constructor", "exists", "wrapper", "other"]
    wrapper: str | None = None
    scaffold_tactic: str | None = None


def normalized_goal_text(goal: str) -> str:
    text = " ".join(str(goal).replace("\n", " ").split())
    if "⊢" in text:
        text = text.split("⊢", 1)[1].strip()
    return text


def _top_level_symbol_index(text: str, symbol: str) -> int:
    depth = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            continue
        if depth == 0 and text.startswith(symbol, index):
            return index
    return -1


def theorem_goal_statement(theorem_code: str) -> str | None:
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
        char = signature[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            if i + 1 < len(signature) and signature[i + 1] == "=":
                i += 2
                continue
            last_colon = i
        i += 1
    if last_colon == -1:
        return None
    return normalized_goal_text(signature[last_colon + 1 :])


def theorem_parameter_names(theorem_code: str) -> list[str]:
    goal = theorem_goal_statement(theorem_code)
    sig_match = re.search(
        r"(?:theorem|lemma)\s+[A-Za-z0-9_']+(.*?):=\s*by",
        theorem_code,
        re.DOTALL,
    )
    if sig_match is None:
        return []
    signature = sig_match.group(1)
    if goal and goal in signature:
        signature = signature.rsplit(goal, 1)[0]
    names: list[str] = []
    for match in re.finditer(r"[\(\{](?P<names>[^:\)\}\[\]]+)\s*:", signature):
        for raw_name in match.group("names").split():
            name = raw_name.strip()
            if not name or name == "_" or name.startswith("["):
                continue
            if name not in names:
                names.append(name)
    return names


def intro_names_from_body(theorem_code: str) -> list[str]:
    names: list[str] = []
    for line in theorem_code.splitlines():
        stripped = line.strip()
        if not stripped.startswith("intro "):
            continue
        for raw_name in stripped[len("intro ") :].split():
            name = raw_name.strip()
            if name and name not in names:
                names.append(name)
    return names


def goal_identifiers(goal: str) -> list[str]:
    identifiers: list[str] = []
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_']*", normalized_goal_text(goal)):
        name = match.group(0)
        if name not in identifiers:
            identifiers.append(name)
    return identifiers


def classify_goal_shape(goal: str) -> GoalShape:
    normalized = normalized_goal_text(goal)
    if not normalized:
        return GoalShape(kind="other")
    if _top_level_symbol_index(normalized, "∧") != -1:
        return GoalShape(kind="constructor", scaffold_tactic="constructor")
    if normalized.startswith("∃ ") or normalized.startswith("Exists "):
        return GoalShape(kind="exists", scaffold_tactic="refine ⟨_, ?_⟩")
    for wrapper in ("ContinuousOn", "Continuous"):
        if normalized.startswith(f"{wrapper} ") or normalized.startswith(f"{wrapper}("):
            return GoalShape(kind="wrapper", wrapper=wrapper)
    for wrapper in ("Monotone", "Antitone", "StrictMono", "StrictAnti"):
        if normalized.startswith(f"{wrapper} ") or normalized.startswith(f"{wrapper}("):
            return GoalShape(kind="intro", wrapper=wrapper, scaffold_tactic="intro x y hxy")
    if normalized.startswith("∀") or normalized.lower().startswith("forall "):
        return GoalShape(kind="intro", scaffold_tactic="repeat intro")
    return GoalShape(kind="other")


def goal_shape_scaffold(goal: str, *, tactic_hints: list[str] | None = None) -> GoalShape | None:
    shape = classify_goal_shape(goal)
    if shape.kind != "exists":
        return shape if shape.kind != "other" else None
    for hint in tactic_hints or []:
        stripped = hint.strip()
        if stripped.startswith("refine ⟨") or stripped.startswith("refine <"):
            return GoalShape(kind="exists", scaffold_tactic=stripped)
    return shape


def normalized_diagnostic_signature(messages: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for message in messages:
        text = re.sub(r"\s+", " ", str(message).strip().lower())
        text = re.sub(r"`[^`]+`", "`_`", text)
        if text and text not in normalized:
            normalized.append(text[:120])
    return tuple(normalized[:3])


def direct_hypothesis_name(theorem_code: str) -> str | None:
    # Locate the theorem signature up to `:= by` (handles both multi-line stubs
    # and single-line formalizer output).
    goal = theorem_goal_statement(theorem_code)
    sig_match = re.search(
        r"(?:theorem|lemma)\s+[A-Za-z0-9_']+(.*?):=\s*by",
        theorem_code,
        re.DOTALL,
    )
    if sig_match is None or goal is None:
        return None
    signature = sig_match.group(1)
    binders = signature.rsplit(goal, 1)[0]
    for match in re.finditer(r"\((?P<name>[A-Za-z0-9_']+)\s*:\s*(?P<body>[^)]*)\)", binders):
        body = normalized_goal_text(match.group("body"))
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
