"""Budget tracking and circuit breakers for the proving harness."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config import MAX_SEARCH_TOOL_CALLS, MAX_TOTAL_TOOL_CALLS

LSP_TOOL_NAMES = frozenset(
    {
        "lean_goal",
        "lean_code_actions",
        "lean_hover_info",
        "lean_diagnostic_messages",
        "lean_leansearch",
        "lean_loogle",
    }
)

NATIVE_SEARCH_TOOL_NAMES = frozenset({"lean_leansearch", "lean_loogle"})


@dataclass
class BudgetTracker:
    """Track tool usage against configured proving budgets."""

    max_search_tool_calls: int = MAX_SEARCH_TOOL_CALLS
    max_total_tool_calls: int = MAX_TOTAL_TOOL_CALLS
    search_tool_calls: int = 0
    total_tool_calls: int = 0
    lsp_tool_calls: int = 0
    native_search_attempts: int = 0
    mathlib_native_mode_uses: int = 0
    sub_agent_calls: int = 0
    tool_history: list[str] = field(default_factory=list)
    sub_agent_history: list[str] = field(default_factory=list)

    def record(self, tool_name: str) -> None:
        self.total_tool_calls += 1
        self.tool_history.append(tool_name)
        if tool_name in {"search", "lean_leansearch", "lean_loogle"}:
            self.search_tool_calls += 1
        if tool_name in LSP_TOOL_NAMES:
            self.lsp_tool_calls += 1
        if tool_name in NATIVE_SEARCH_TOOL_NAMES:
            self.native_search_attempts += 1

    def record_native_search_attempt(self) -> None:
        self.native_search_attempts += 1

    def record_mathlib_native_mode_use(self) -> None:
        self.mathlib_native_mode_uses += 1

    def record_sub_agent_call(self, name: str) -> None:
        self.sub_agent_calls += 1
        self.sub_agent_history.append(name)

    def can_continue(self) -> bool:
        return self.total_tool_calls < self.max_total_tool_calls

    def can_search(self) -> bool:
        return self.search_tool_calls < self.max_search_tool_calls

    def snapshot(self) -> dict[str, int | list[str]]:
        return {
            "max_search_tool_calls": self.max_search_tool_calls,
            "max_total_tool_calls": self.max_total_tool_calls,
            "search_tool_calls": self.search_tool_calls,
            "total_tool_calls": self.total_tool_calls,
            "lsp_tool_calls": self.lsp_tool_calls,
            "native_search_attempts": self.native_search_attempts,
            "mathlib_native_mode_uses": self.mathlib_native_mode_uses,
            "sub_agent_calls": self.sub_agent_calls,
            "tool_history": list(self.tool_history),
            "sub_agent_history": list(self.sub_agent_history),
        }
