from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.observability import BudgetTracker
from src.prover import VerificationHarness, direct_hypothesis_name, suggest_fast_path_tactics
from src.prover.file_controller import ProofFileController
from src.prover.tools import REPLToolDispatcher
from src.tools import ToolCall


def test_direct_hypothesis_fast_path() -> None:
    theorem = (
        "theorem demo\n"
        "    (h : 1 = 1) :\n"
        "    1 = 1 := by\n"
        "  sorry\n"
    )
    assert direct_hypothesis_name(theorem) == "h"
    assert suggest_fast_path_tactics(theorem)[0] == "exact h"
    assert direct_hypothesis_name("theorem demo : True := by\n  sorry\n") is None
    assert direct_hypothesis_name("theorem demo\n    (h : 1 = 1) :\n    2 = 2 := by\n  sorry\n") is None
    assert suggest_fast_path_tactics("theorem nkpc_demo : nkpc x y z 0 = 0 := by\n  sorry\n")[0] == "simpa [nkpc]"


@pytest.mark.anyio
async def test_verification_harness_compile_fallback(tmp_path, monkeypatch) -> None:
    import src.prover.harness as harness_module

    monkeypatch.setattr(harness_module, "REPL_ENABLED", False)
    monkeypatch.setattr(
        harness_module,
        "compile_check",
        lambda code, **kwargs: {
            "success": "norm_num" in code,
            "output": "",
            "errors": [] if "norm_num" in code else ["no proof"],
            "classified_errors": [],
        },
    )

    harness = VerificationHarness(
        file_controller=ProofFileController(workspace_root=tmp_path),
        budget_tracker=BudgetTracker(),
    )
    result = await harness.verify(
        "theorem demo : 1 + 1 = 2 := by\n  norm_num\n",
        "job_compile",
        tactic_hint=None,
    )
    assert result.status == "completed"
    assert result.result["status"] == "verified"


@pytest.mark.anyio
async def test_verification_harness_repl_success(tmp_path, monkeypatch) -> None:
    import src.prover.harness as harness_module

    class FakeRepl:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def start_proof(self, theorem_with_sorry: str, timeout=None):
            self.theorem = theorem_with_sorry
            return SimpleNamespace(state_id=1, goals=["goal"], is_solved=False)

        def apply_tactic(self, tactic, timeout=None):
            self.tactic = tactic
            return SimpleNamespace(has_errors=lambda: False, proof_status="Completed")

        def materialize_proof(self):
            return self.theorem.replace("sorry", "norm_num")

        def verify_materialized_proof(self, filename: str, timeout=None):
            return {"success": True, "output": "", "errors": []}

    monkeypatch.setattr(harness_module, "LeanREPLSession", lambda timeout=None: FakeRepl())
    monkeypatch.setattr(harness_module, "REPL_ENABLED", True)

    harness = VerificationHarness(
        file_controller=ProofFileController(workspace_root=tmp_path),
        budget_tracker=BudgetTracker(),
    )
    result = await harness.verify("theorem demo : 1 + 1 = 2 := by\n  sorry\n", "job_repl")
    assert result.status == "completed"
    assert result.result["verification_trace"]["repl_used"] is True


@pytest.mark.anyio
async def test_verification_harness_repl_continue_then_compile_failure(tmp_path, monkeypatch) -> None:
    import src.prover.harness as harness_module

    class ErrorResponse:
        def has_errors(self) -> bool:
            return True

    class FakeRepl:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def start_proof(self, theorem_with_sorry: str, timeout=None):
            self.theorem = theorem_with_sorry
            return SimpleNamespace(state_id=1, goals=["goal"], is_solved=False)

        def apply_tactic(self, tactic, timeout=None):
            return ErrorResponse()

        def materialize_proof(self):
            return self.theorem

        def verify_materialized_proof(self, filename: str, timeout=None):
            return {"success": False, "output": "", "errors": ["bad"]}

    monkeypatch.setattr(harness_module, "LeanREPLSession", lambda timeout=None: FakeRepl())
    monkeypatch.setattr(harness_module, "REPL_ENABLED", True)
    monkeypatch.setattr(
        harness_module,
        "compile_check",
        lambda code, **kwargs: {"success": False, "output": "", "errors": ["bad"]},
    )

    harness = VerificationHarness(
        file_controller=ProofFileController(workspace_root=tmp_path),
        budget_tracker=BudgetTracker(),
    )
    result = await harness.verify("theorem demo : True := by\n  sorry\n", "job_repl_fail")
    assert result.status == "failed"


@pytest.mark.anyio
async def test_repl_tool_dispatcher_handles_read_write_and_tactic(tmp_path) -> None:
    class FakeState:
        def __init__(self) -> None:
            self.state_id = 1
            self.goals = ["goal"]
            self.is_solved = False

    class FakeResponse:
        def __init__(self, completed: bool = False) -> None:
            self.proof_state = 2
            self.goals = [] if completed else ["goal"]
            self.proof_status = "Completed" if completed else "Incomplete"

        def has_errors(self) -> bool:
            return False

    class FakeRepl:
        def __init__(self) -> None:
            self.proof_state = FakeState()
            self.current = "theorem demo : True := by\n  sorry\n"

        def start_proof(self, theorem_code: str):
            self.current = theorem_code
            return self.proof_state

        def apply_tactic(self, state_id: int, tactic: str):
            self.current = self.current.replace("sorry", tactic)
            return FakeResponse(completed=True)

        def materialize_proof(self):
            return self.current

        def get_goal_state(self, state_id: int):
            return self.proof_state

    controller = ProofFileController(workspace_root=tmp_path)
    controller.initialize("job", "theorem demo : True := by\n  sorry\n")
    dispatcher = REPLToolDispatcher(
        repl=FakeRepl(),
        theorem_code="theorem demo : True := by\n  sorry\n",
        file_controller=controller,
        job_id="job",
    )
    await dispatcher.initialize()

    read_result = dispatcher.handle_tool_call(ToolCall("1", "read_current_code", {}))
    write_result = dispatcher.handle_tool_call(
        ToolCall("2", "write_current_code", {"code": "theorem demo : True := by\n  sorry\n"})
    )
    tactic_result = dispatcher.handle_tool_call(ToolCall("3", "apply_tactic", {"tactic": "trivial"}))

    assert "theorem demo" in read_result.content
    assert "Updated theorem code" in write_result.content
    assert "All goals solved" in tactic_result.content
