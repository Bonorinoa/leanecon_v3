"""Minimal finite-state machine for the mathlib-native prover path.

This module implements the Sprint 26 state set from
``Sprint26_StateMachine_Design.md``. The state machine is intentionally small:
it tracks the current strategic prover state, validates explicit transitions,
and exposes state-specific configuration for later prompt/tool constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class ProverState(Enum):
    """Strategic states for the mathlib-native prover harness."""

    Synthesizing = "Synthesizing"
    Stalled = "Stalled"
    Decomposing = "Decomposing"
    Rescue = "Rescue"
    Verified = "Verified"
    Failed = "Failed"


@dataclass(frozen=True)
class StateConfig:
    """Constraint hints associated with a strategic prover state.

    These configs are intentionally descriptive only. They do not enforce tool,
    prompt, or memory behavior until later state-machine integration work.
    """

    allowed_tools: list[str] = field(default_factory=list)
    prompt_rules: dict[str, Any] = field(default_factory=dict)
    memory_filter: str = "none"
    max_tool_calls: int | None = None
    allow_decompose: bool = False
    terminal: bool = False

    def copy(self) -> StateConfig:
        """Return a shallow mutable-field copy for safe caller use."""
        return replace(
            self,
            allowed_tools=list(self.allowed_tools),
            prompt_rules=dict(self.prompt_rules),
        )


_ALL_PROVER_TOOLS = [
    "apply_tactic",
    "compile_current_code",
    "get_goals",
    "lean_code_actions",
    "lean_diagnostic_messages",
    "lean_file_outline",
    "lean_goal",
    "lean_hover_info",
    "lean_leansearch",
    "lean_local_search",
    "lean_loogle",
    "lean_run_code",
    "memory_retrieve",
    "read_current_code",
    "write_current_code",
]


_STATE_CONFIGS: dict[ProverState, StateConfig] = {
    ProverState.Synthesizing: StateConfig(
        allowed_tools=list(_ALL_PROVER_TOOLS),
        prompt_rules={
            "mode": "synthesize",
            "guidance": "Try direct proof steps first; use search and diagnostics as needed.",
        },
        memory_filter="broad",
        max_tool_calls=None,
        allow_decompose=True,
    ),
    ProverState.Stalled: StateConfig(
        allowed_tools=[
            "apply_tactic",
            "get_goals",
            "lean_code_actions",
            "lean_diagnostic_messages",
            "lean_goal",
            "lean_leansearch",
            "lean_local_search",
            "lean_loogle",
            "memory_retrieve",
            "read_current_code",
        ],
        prompt_rules={
            "mode": "recover_from_stall",
            "guidance": "Inspect the current goal and recent failure before trying a new tactic.",
        },
        memory_filter="failure_focused",
        max_tool_calls=4,
        allow_decompose=True,
    ),
    ProverState.Decomposing: StateConfig(
        allowed_tools=[
            "apply_tactic",
            "compile_current_code",
            "get_goals",
            "lean_code_actions",
            "lean_goal",
            "lean_leansearch",
            "lean_local_search",
            "read_current_code",
            "write_current_code",
        ],
        prompt_rules={
            "mode": "decompose",
            "guidance": "Create the smallest useful helper lemma or subgoal split.",
        },
        memory_filter="subgoal_focused",
        max_tool_calls=6,
        allow_decompose=True,
    ),
    ProverState.Rescue: StateConfig(
        allowed_tools=[
            "apply_tactic",
            "get_goals",
            "lean_leansearch",
            "lean_local_search",
        ],
        prompt_rules={
            "mode": "rescue",
            "guidance": "Use one narrow lookup or tactic to recover from the specific failure.",
        },
        memory_filter="rescue_identifier",
        max_tool_calls=2,
        allow_decompose=False,
    ),
    ProverState.Verified: StateConfig(
        allowed_tools=[],
        prompt_rules={"mode": "terminal"},
        memory_filter="none",
        max_tool_calls=0,
        allow_decompose=False,
        terminal=True,
    ),
    ProverState.Failed: StateConfig(
        allowed_tools=[],
        prompt_rules={"mode": "terminal"},
        memory_filter="none",
        max_tool_calls=0,
        allow_decompose=False,
        terminal=True,
    ),
}


def get_state_config(state: ProverState) -> StateConfig:
    """Return the descriptive config for ``state``."""
    return _STATE_CONFIGS[state].copy()


class StateMachine:
    """Validate and track prover state transitions.

    The allowed transitions mirror the initial rules in the Sprint 26 design:
    stalls move synthesis into recovery, rescue is a one-shot detour back to
    synthesis, verified/failed are terminal, and failure is allowed from any
    non-terminal state.
    """

    _ALLOWED_TRANSITIONS: dict[ProverState, frozenset[ProverState]] = {
        ProverState.Synthesizing: frozenset(
            {
                ProverState.Stalled,
                ProverState.Rescue,
                ProverState.Verified,
                ProverState.Failed,
            }
        ),
        ProverState.Stalled: frozenset(
            {
                ProverState.Synthesizing,
                ProverState.Decomposing,
                ProverState.Rescue,
                ProverState.Failed,
            }
        ),
        ProverState.Decomposing: frozenset(
            {
                ProverState.Verified,
                ProverState.Failed,
            }
        ),
        ProverState.Rescue: frozenset(
            {
                ProverState.Synthesizing,
                ProverState.Failed,
            }
        ),
        ProverState.Verified: frozenset(),
        ProverState.Failed: frozenset(),
    }

    def __init__(self, initial_state: ProverState = ProverState.Synthesizing) -> None:
        self.current_state = initial_state

    def reset(self, state: ProverState = ProverState.Synthesizing) -> None:
        """Reset the current state for a fresh prover run."""
        self.current_state = state

    def get_current_config(self) -> StateConfig:
        """Return the descriptive config for the current state."""
        return get_state_config(self.current_state)

    def can_transition(self, next_state: ProverState) -> bool:
        """Return whether ``next_state`` is valid from the current state."""
        return next_state in self._ALLOWED_TRANSITIONS[self.current_state]

    def transition(self, next_state: ProverState, *, reason: str | None = None) -> ProverState:
        """Move to ``next_state`` or raise ``ValueError`` for an invalid edge.

        ``reason`` is accepted for call-site readability and future
        observability, but this minimal implementation does not persist it.
        """
        del reason
        if next_state == self.current_state:
            return self.current_state
        if not self.can_transition(next_state):
            raise ValueError(
                f"Invalid prover state transition: "
                f"{self.current_state.value} -> {next_state.value}"
            )
        self.current_state = next_state
        return self.current_state
