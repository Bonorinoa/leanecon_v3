"""REPL-backed tool dispatch helpers for the prover harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.prover.file_controller import ProofFileController
from src.tools import ToolCall, ToolResult

try:  # pragma: no cover - optional runtime dependency
    from lean_interact.interface import LeanError
except ModuleNotFoundError:  # pragma: no cover
    class LeanError(Exception):
        message: str = ""


def _format_goals(goals: list[str]) -> str:
    if not goals:
        return "All goals solved."
    lines = ["Current goals:"]
    for index, goal in enumerate(goals, start=1):
        lines.append(f"  {index}. {goal}")
    return "\n".join(lines)


def _collect_error_messages(response: Any) -> list[str]:
    if isinstance(response, LeanError):
        return [response.message]
    if hasattr(response, "get_errors"):
        return [message.data for message in response.get_errors() if getattr(message, "data", "")]
    return []


@dataclass
class REPLToolDispatcher:
    """Dispatch prover tool calls through a live LeanInteract session."""

    repl: Any
    theorem_code: str
    file_controller: ProofFileController | None = None
    job_id: str | None = None
    budget_tracker: Any = None  # BudgetTracker | None — optional to avoid circular import
    current_state_id: int | None = None
    tactic_history: list[str] = field(default_factory=list)
    goal_history: list[list[str]] = field(default_factory=list)

    async def initialize(self) -> dict[str, Any]:
        state = self.repl.start_proof(self.theorem_code)
        self.current_state_id = state.state_id
        self.goal_history.append(list(state.goals))
        self._sync_current_code()
        return {
            "goals": list(state.goals),
            "is_solved": state.is_solved,
            "message": f"Proof initialized with {len(state.goals)} goal(s).",
        }

    def handle_tool_call(self, tool_call: ToolCall) -> ToolResult:
        if tool_call.name == "read_current_code":
            return ToolResult(tool_call.id, self._read_current_code())
        if tool_call.name in {"compile_current_code", "get_goals"}:
            return ToolResult(tool_call.id, self._get_goals())
        if tool_call.name == "write_current_code":
            return self._write_current_code(tool_call)
        if tool_call.name == "apply_tactic":
            tactic = str(tool_call.arguments.get("tactic", "")).strip()
            if not tactic:
                return ToolResult(tool_call.id, "Missing tactic.", is_error=True)
            return self._apply_tactic(tool_call.id, tactic)
        return ToolResult(tool_call.id, f"Unknown tool: {tool_call.name}", is_error=True)

    def build_final_code(self) -> str:
        if self.file_controller is not None and self.job_id is not None:
            return self.file_controller.read_current_code(self.job_id)
        return self.repl.materialize_proof()

    def is_solved(self) -> bool:
        """Return whether the active REPL state has no remaining goals."""

        state = self._current_state()
        if bool(getattr(state, "is_solved", False)):
            return True
        goals = list(getattr(state, "goals", []) or [])
        return len(goals) == 0

    def _read_current_code(self) -> str:
        if self.file_controller is not None and self.job_id is not None:
            return self.file_controller.read_current_code(self.job_id)
        return self.repl.materialize_proof()

    def _get_goals(self) -> str:
        state = self._current_state()
        return _format_goals(list(state.goals))

    def get_analysis_context(self) -> dict[str, Any]:
        state = self._current_state()
        goals = list(getattr(state, "goals", []) or [])
        if not goals and self.goal_history:
            goals = list(self.goal_history[-1])
        return {
            "goals": goals,
            "tactic_history": list(self.tactic_history),
        }

    def _apply_tactic(self, call_id: str, tactic: str) -> ToolResult:
        state = self._current_state()
        response = self.repl.apply_tactic(state.state_id, tactic)
        if isinstance(response, LeanError) or response.has_errors():
            error_messages = _collect_error_messages(response)
            if isinstance(response, LeanError):
                content = "\n".join(error_messages) if error_messages else response.message
            else:
                content = (
                    "\n".join(error_messages)
                    if error_messages
                    else f"Tactic failed: {tactic}"
                )
            return ToolResult(call_id, content, is_error=True)

        self.current_state_id = response.proof_state
        self.tactic_history.append(tactic)
        self.goal_history.append(list(response.goals))
        self._sync_current_code()

        if getattr(response, "proof_status", "") == "Completed":
            return ToolResult(call_id, "Proof complete! All goals solved.")

        return ToolResult(call_id, _format_goals(list(response.goals)))

    def _write_current_code(self, tool_call: ToolCall) -> ToolResult:
        new_code = str(tool_call.arguments.get("theorem_code") or tool_call.arguments.get("code") or "").strip()
        if not new_code:
            return ToolResult(tool_call.id, "Missing theorem_code.", is_error=True)

        self.theorem_code = new_code
        if self.file_controller is not None and self.job_id is not None:
            self.file_controller.write_current_code(self.job_id, new_code)

        try:
            state = self.repl.start_proof(new_code)
        except Exception as exc:
            return ToolResult(tool_call.id, str(exc), is_error=True)

        self.current_state_id = state.state_id
        self.tactic_history.clear()
        self.goal_history = [list(state.goals)]
        self._sync_current_code()
        return ToolResult(tool_call.id, "Updated theorem code and restarted the REPL proof.")

    def _current_state(self):
        if self.current_state_id is None:
            raise RuntimeError("Call initialize() before using REPL tool calls.")

        if hasattr(self.repl, "get_goal_state"):
            state = self.repl.get_goal_state(self.current_state_id)
        else:
            state = getattr(self.repl, "proof_state", None)
            if state is None:
                raise RuntimeError("The active REPL state is unavailable.")
            if getattr(state, "state_id", None) != self.current_state_id:
                raise RuntimeError("The active REPL state does not match the dispatcher state.")

        return state

    def _sync_current_code(self) -> None:
        if self.file_controller is None or self.job_id is None:
            return
        self.file_controller.write_current_code(self.job_id, self.repl.materialize_proof())
        if self.tactic_history:
            self.file_controller.checkpoint(self.job_id, len(self.tactic_history))
