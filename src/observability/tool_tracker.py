"""Budget tracking and circuit breakers for the proving harness."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config import MAX_SEARCH_TOOL_CALLS, MAX_TOTAL_TOOL_CALLS


@dataclass
class BudgetTracker:
    """Track tool usage against configured proving budgets."""

    max_search_tool_calls: int = MAX_SEARCH_TOOL_CALLS
    max_total_tool_calls: int = MAX_TOTAL_TOOL_CALLS
    search_tool_calls: int = 0
    total_tool_calls: int = 0
    sub_agent_calls: int = 0
    tool_history: list[str] = field(default_factory=list)
    sub_agent_history: list[str] = field(default_factory=list)

    def record(self, tool_name: str) -> None:
        self.total_tool_calls += 1
        self.tool_history.append(tool_name)
        if tool_name == "search":
            self.search_tool_calls += 1

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
            "sub_agent_calls": self.sub_agent_calls,
            "tool_history": list(self.tool_history),
            "sub_agent_history": list(self.sub_agent_history),
        }
