from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.guardrails.semantic_frame import extract_claim_frame, extract_stub_frame
from src.guardrails.vacuity import is_vacuous
from src.prover.repl import ReplToolOrchestrator, run_repl_fast_path, sync_repl_trace
from src.prover.tools import REPLToolDispatcher
from src.tools import ToolCall, ToolResult


def test_semantic_frame_handles_universal_and_fallback_claims() -> None:
    universal = extract_claim_frame("For all x, utility is increasing.")
    fallback = extract_claim_frame("Obscure text")
    stub = extract_stub_frame("import LeanEcon.Preamble.Dynamic.BellmanEquation\n")

    assert universal.quantifier_shape == "universal"
    assert fallback.concepts
    assert "bellman" in stub.concepts


def test_vacuity_empty_and_non_vacuous_cases() -> None:
    assert is_vacuous("") is True
    assert is_vacuous("theorem demo : 1 = 1 := by\n  rfl\n") is False


def test_repl_tool_helpers_cover_miscellaneous_branches() -> None:
    import src.prover.tools as tools_module

    assert tools_module._format_goals([]) == "All goals solved."
    assert "Current goals" in tools_module._format_goals(["g"])
    assert tools_module._collect_error_messages(object()) == []


@pytest.mark.anyio
async def test_repl_validator_exception_and_invalid_paths(monkeypatch) -> None:
    import src.guardrails.repl_validator as rv

    monkeypatch.setattr(rv, "FORMALIZER_REPL_VALIDATION_ENABLED", True)
    monkeypatch.setattr(rv, "_LEAN_INTERACT_AVAILABLE", True)

    class BrokenSession:
        def __enter__(self):
            raise RuntimeError("boom")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rv, "_LeanREPLSession", BrokenSession)
    assert await rv.validate_identifiers(["x"], ["Mathlib"]) == {}
    assert await rv.get_type_signature("x", ["Mathlib"]) is None

    class FakeResponse:
        def __init__(self, valid: bool) -> None:
            self._valid = valid
            self.messages = []

        def lean_code_is_valid(self):
            return self._valid

    class InvalidSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_command(self, command: str):
            return FakeResponse(False)

    monkeypatch.setattr(rv, "_LeanREPLSession", InvalidSession)
    monkeypatch.setattr(rv, "_LeanError", type(None))
    assert await rv.get_type_signature("x", ["Mathlib"]) is None


@pytest.mark.anyio
async def test_repl_tool_dispatcher_error_paths(tmp_path) -> None:
    class FakeLeanError(Exception):
        def __init__(self, message: str) -> None:
            self.message = message

    class ErrorResponse:
        def has_errors(self) -> bool:
            return True

        def get_errors(self):
            return [SimpleNamespace(data="bad tactic")]

    class FakeState:
        state_id = 7
        goals = ["goal"]
        is_solved = False

    class FakeRepl:
        def __init__(self) -> None:
            self.proof_state = FakeState()
            self.code = "theorem demo : True := by\n  sorry\n"

        def start_proof(self, theorem_code: str):
            if "explode" in theorem_code:
                raise RuntimeError("restart failed")
            self.code = theorem_code
            return self.proof_state

        def apply_tactic(self, state_id: int, tactic: str):
            if tactic == "lean_error":
                return FakeLeanError("lean broken")
            return ErrorResponse()

        def materialize_proof(self):
            return self.code

    import src.prover.tools as tools_module

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tools_module, "LeanError", FakeLeanError)

    dispatcher = REPLToolDispatcher(repl=FakeRepl(), theorem_code="theorem demo : True := by\n  sorry\n")
    with pytest.raises(RuntimeError):
        dispatcher.is_solved()

    init_payload = await dispatcher.initialize()
    assert init_payload["message"].startswith("Proof initialized")

    assert dispatcher.handle_tool_call(ToolCall("u", "unknown_tool", {})).is_error is True
    assert dispatcher.handle_tool_call(ToolCall("m", "apply_tactic", {})).is_error is True
    assert dispatcher.handle_tool_call(ToolCall("l", "apply_tactic", {"tactic": "lean_error"})).is_error is True
    assert dispatcher.handle_tool_call(ToolCall("e", "apply_tactic", {"tactic": "bad"})).is_error is True
    assert dispatcher.handle_tool_call(ToolCall("w", "write_current_code", {})).is_error is True
    assert dispatcher.handle_tool_call(
        ToolCall("x", "write_current_code", {"code": "explode"})
    ).is_error is True
    monkeypatch.undo()


@pytest.mark.anyio
async def test_repl_tool_dispatcher_misc_success_branches(tmp_path) -> None:
    class FakeState:
        def __init__(self, state_id: int, goals: list[str], solved: bool = False) -> None:
            self.state_id = state_id
            self.goals = goals
            self.is_solved = solved

    class PartialResponse:
        proof_state = 2
        goals = ["next_goal"]
        proof_status = "InProgress"

        def has_errors(self) -> bool:
            return False

    class FakeRepl:
        def __init__(self) -> None:
            self.proof_state = FakeState(1, [], solved=False)
            self.current = "theorem demo : True := by\n  sorry\n"

        def start_proof(self, theorem_code: str):
            self.current = theorem_code
            self.proof_state = FakeState(1, [], solved=False)
            return self.proof_state

        def apply_tactic(self, state_id: int, tactic: str):
            return PartialResponse()

        def materialize_proof(self):
            return self.current

    dispatcher = REPLToolDispatcher(repl=FakeRepl(), theorem_code="theorem demo : True := by\n  sorry\n")
    await dispatcher.initialize()
    assert dispatcher.build_final_code().startswith("theorem demo")
    assert dispatcher.is_solved() is True
    assert dispatcher.handle_tool_call(ToolCall("g", "get_goals", {})).content == "All goals solved."
    assert dispatcher.handle_tool_call(ToolCall("c", "compile_current_code", {})).content == "All goals solved."
    assert dispatcher.get_analysis_context()["goals"] == []
    tactic_result = dispatcher.handle_tool_call(ToolCall("t", "apply_tactic", {"tactic": "simp"}))
    assert "Current goals" in tactic_result.content

    controller = REPLToolDispatcher(
        repl=FakeRepl(),
        theorem_code="theorem demo : True := by\n  sorry\n",
        file_controller=SimpleNamespace(
            read_current_code=lambda job_id: "stored",
            write_current_code=lambda job_id, code: None,
            checkpoint=lambda job_id, step: None,
        ),
        job_id="job",
    )
    await controller.initialize()
    assert controller.build_final_code() == "stored"
    assert controller.handle_tool_call(ToolCall("r", "read_current_code", {})).content == "stored"

    mismatch = REPLToolDispatcher(repl=SimpleNamespace(proof_state=None), theorem_code="theorem demo : True := by\n  sorry\n")
    mismatch.current_state_id = 1
    with pytest.raises(RuntimeError):
        mismatch._current_state()

    mismatch.repl = SimpleNamespace(proof_state=SimpleNamespace(state_id=2))
    with pytest.raises(RuntimeError):
        mismatch._current_state()

    solved = REPLToolDispatcher(
        repl=SimpleNamespace(
            proof_state=SimpleNamespace(state_id=1, goals=["g"], is_solved=True),
            materialize_proof=lambda: "raw-code",
        ),
        theorem_code="theorem demo : True := by\n  sorry\n",
    )
    solved.current_state_id = 1
    assert solved.is_solved() is True
    assert solved._read_current_code() == "raw-code"


@pytest.mark.anyio
async def test_repl_fast_path_helpers_cover_success_and_fallback(tmp_path) -> None:
    verification_trace = {}
    repl_report = {"used": True, "success": True, "attempts": [{"tactic": "simp"}], "fallback_reason": None}
    sync_repl_trace(verification_trace, repl_report)
    assert verification_trace["repl_attempt_count"] == 1

    class Controller:
        def __init__(self) -> None:
            self.writes = []
            self.checkpoints = []

        def write_current_code(self, job_id: str, code: str) -> None:
            self.writes.append((job_id, code))

        def checkpoint(self, job_id: str, step: int) -> None:
            self.checkpoints.append((job_id, step))

    class Telemetry:
        def __init__(self) -> None:
            self.calls = 0

        def record_lean(self, _started: float) -> None:
            self.calls += 1

    controller = Controller()
    telemetry = Telemetry()
    stages = {"repl_fast_path": 0.0}
    outcomes = {}
    emitted = []

    async def fast_path(_repl, _code, **kwargs):
        return {
            "used": True,
            "success": True,
            "attempts": [{"tactic": "simp"}],
            "fallback_reason": None,
            "candidate_code": "theorem demo : True := by\n  trivial\n",
            "candidate_result": {"success": True},
        }

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    result, repl_report, check = await run_repl_fast_path(
        repl_session_factory=Session,
        repl_fast_path_fn=fast_path,
        repl_validation_result_fn=lambda report: {"validated": True},
        current_code="theorem demo : True := by\n  sorry\n",
        job_id="job",
        max_steps=4,
        tactic_hint=None,
        telemetry=telemetry,
        file_controller=controller,
        attempts=[],
        repl_report={"used": False, "success": False, "attempts": [], "fallback_reason": None},
        verification_trace={},
        stage_timings_ms=stages,
        stage_outcomes=outcomes,
        emit_progress=lambda stage, payload: emitted.append((stage, payload)),
        build_success_status=lambda code, compile_result, report: {"code": code, "compile": compile_result, "report": report},
    )
    assert result is not None
    assert check is None
    assert emitted[0][0] == "repl_fast_path"

    async def bad_fast_path(*args, **kwargs):
        raise RuntimeError("fail")

    result, repl_report, check = await run_repl_fast_path(
        repl_session_factory=Session,
        repl_fast_path_fn=bad_fast_path,
        repl_validation_result_fn=lambda report: {"validated": False},
        current_code="x",
        job_id="job",
        max_steps=1,
        tactic_hint="simp",
        telemetry=None,
        file_controller=controller,
        attempts=[],
        repl_report={"used": False, "success": False, "attempts": [], "fallback_reason": None},
        verification_trace={},
        stage_timings_ms={"repl_fast_path": 0.0},
        stage_outcomes={},
        emit_progress=lambda stage, payload: emitted.append((stage, payload)),
        build_success_status=lambda *args, **kwargs: {},
    )
    assert result is None
    assert check == {"validated": False}

    async def incomplete_fast_path(*args, **kwargs):
        return {
            "used": True,
            "success": True,
            "attempts": [],
            "fallback_reason": None,
            "candidate_code": None,
            "candidate_result": None,
        }

    result, repl_report, check = await run_repl_fast_path(
        repl_session_factory=Session,
        repl_fast_path_fn=incomplete_fast_path,
        repl_validation_result_fn=lambda report: {"validated": False},
        current_code="x",
        job_id="job",
        max_steps=1,
        tactic_hint=None,
        telemetry=None,
        file_controller=controller,
        attempts=[],
        repl_report={"used": False, "success": False, "attempts": [], "fallback_reason": None},
        verification_trace={},
        stage_timings_ms={"repl_fast_path": 0.0},
        stage_outcomes={},
        emit_progress=lambda stage, payload: emitted.append((stage, payload)),
        build_success_status=lambda *args, **kwargs: {},
    )
    assert result is None


@pytest.mark.anyio
async def test_repl_tool_orchestrator_branches() -> None:
    class FakeDispatcher:
        def __init__(self, *args, **kwargs) -> None:
            self.initialized = False

        async def initialize(self):
            self.initialized = True

        def handle_tool_call(self, tool_call: ToolCall) -> ToolResult:
            if tool_call.name == "apply_tactic":
                return ToolResult(tool_call.id, "bad tactic", is_error=True)
            return ToolResult(tool_call.id, "All goals solved.")

        def get_analysis_context(self):
            return {"goals": ["g"], "tactic_history": ["simp"]}

        def is_solved(self):
            return True

        def build_final_code(self):
            return "theorem demo : True := by\n  trivial\n"

    import src.prover.repl as repl_module

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repl_module, "REPLToolDispatcher", FakeDispatcher)

    telemetry_calls = []
    orchestrator = ReplToolOrchestrator(
        repl=object(),
        theorem_code="theorem demo : True := by\n  sorry\n",
        file_controller=SimpleNamespace(),
        job_id="job",
        budget_tracker=SimpleNamespace(record_sub_agent_call=lambda name: telemetry_calls.append(name), tool_history=[], snapshot=lambda: {}),
        telemetry=SimpleNamespace(record_lean=lambda started: telemetry_calls.append("lean")),
        theorem_name="demo",
        attempts=[],
        verification_trace={},
        timed_compile_check=lambda telemetry, code, filename: {"success": True},
        sync_classified_errors=lambda trace, result: [],
        build_status=lambda *args, **kwargs: {"status": "failed"},
        completed_status=lambda **kwargs: {"status": "completed"},
        goal_analyst_hint_fn=lambda **kwargs: "try simp",
    )
    await orchestrator.initialize()
    assert orchestrator.handles("apply_tactic") is True
    error_result = orchestrator.handle_tool_call(ToolCall("1", "apply_tactic", {"tactic": "x"}), read_without_act=0)
    read_result = orchestrator.handle_tool_call(ToolCall("2", "read_current_code", {}), read_without_act=3)
    assert "Goal Analyst Hint" in error_result.content
    assert "[NOTE]" in read_result.content
    assert orchestrator.should_finalize("apply_tactic", "All goals solved.") is True
    assert orchestrator.terminal_status()["status"] == "completed"

    failing = ReplToolOrchestrator(
        repl=object(),
        theorem_code="theorem demo : True := by\n  sorry\n",
        file_controller=SimpleNamespace(),
        job_id="job",
        budget_tracker=SimpleNamespace(record_sub_agent_call=lambda name: None, tool_history=[], snapshot=lambda: {}),
        telemetry=None,
        theorem_name="demo",
        attempts=[],
        verification_trace={},
        timed_compile_check=lambda telemetry, code, filename: {"success": False},
        sync_classified_errors=lambda trace, result: [],
        build_status=lambda *args, **kwargs: {"status": "failed"},
        completed_status=lambda **kwargs: {"status": "completed"},
        goal_analyst_hint_fn=lambda **kwargs: None,
    )
    with pytest.raises(RuntimeError):
        failing.handle_tool_call(ToolCall("0", "read_current_code", {}), read_without_act=0)
    with pytest.raises(RuntimeError):
        failing.terminal_status()
    assert failing.should_finalize(None, None) is False

    await failing.initialize()
    failing.dispatcher = SimpleNamespace(is_solved=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert failing.should_finalize(None, None) is False
    failing.dispatcher = SimpleNamespace(build_final_code=lambda: "theorem demo : True := by\n  trivial\n")
    assert failing.terminal_status()["status"] == "failed"

    passthrough = ReplToolOrchestrator(
        repl=object(),
        theorem_code="theorem demo : True := by\n  sorry\n",
        file_controller=SimpleNamespace(),
        job_id="job",
        budget_tracker=SimpleNamespace(record_sub_agent_call=lambda name: None, tool_history=[], snapshot=lambda: {}),
        telemetry=None,
        theorem_name="demo",
        attempts=[],
        verification_trace={},
        timed_compile_check=lambda telemetry, code, filename: {"success": True},
        sync_classified_errors=lambda trace, result: [],
        build_status=lambda *args, **kwargs: {"status": "failed"},
        completed_status=lambda **kwargs: {"status": "completed"},
        goal_analyst_hint_fn=lambda **kwargs: None,
    )
    await passthrough.initialize()
    ok = passthrough.handle_tool_call(ToolCall("3", "get_goals", {}), read_without_act=0)
    assert ok.content == "All goals solved."
    monkeypatch.undo()
