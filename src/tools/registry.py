"""ToolSpec registry for Lean-specific tool calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: dict[str, Any] = field(default_factory=dict)
    lean_specific: bool = False
    estimated_cost: str = "low"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs = {spec.name: spec for spec in specs or []}

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list(self) -> list[ToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec("read_current_code", "Read the current proof file.", lean_specific=True),
            ToolSpec("lean_run_code", "Compile a Lean code snippet.", lean_specific=True),
            ToolSpec("compile_current_code", "Compile the current proof file.", lean_specific=True),
            ToolSpec("get_goals", "Read the current Lean goals.", lean_specific=True),
            ToolSpec(
                "write_current_code",
                "Write updated Lean proof code.",
                {"code": {"type": "string"}},
                lean_specific=True,
            ),
            ToolSpec(
                "apply_tactic",
                "Apply one Lean tactic to the current goal state.",
                {"tactic": {"type": "string"}},
                lean_specific=True,
            ),
            ToolSpec("lean_goal", "Query Lean goals via LSP when available.", lean_specific=True),
            ToolSpec(
                "lean_code_actions",
                "Query Lean code actions via LSP when available.",
                lean_specific=True,
            ),
            ToolSpec(
                "lean_hover_info",
                "Query Lean hover/type information via LSP when available.",
                lean_specific=True,
            ),
            ToolSpec(
                "lean_diagnostic_messages",
                "Query Lean diagnostics via LSP when available.",
                {"severity": {"type": "string"}},
                lean_specific=True,
            ),
            ToolSpec(
                "lean_leansearch",
                "Search Mathlib by natural-language theorem intent.",
                {"query": {"type": "string"}, "num_results": {"type": "integer"}},
                lean_specific=True,
            ),
            ToolSpec(
                "lean_local_search",
                "Search local Lean declarations by name or prefix.",
                {"query": {"type": "string"}, "limit": {"type": "integer"}},
                lean_specific=True,
            ),
            ToolSpec(
                "lean_file_outline",
                "Read imports and declaration outlines for the current Lean file.",
                {"max_declarations": {"type": "integer"}},
                lean_specific=True,
            ),
            ToolSpec(
                "lean_loogle",
                "Search Mathlib by declaration name or type pattern.",
                {"query": {"type": "string"}, "num_results": {"type": "integer"}},
                lean_specific=True,
            ),
            ToolSpec("memory_retrieve", "Retrieve similar episodic proof traces."),
        ]
    )
