from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.formalizer.models import FaithfulnessAssessment, FormalizationPacket, ParseCheck
from src.memory.store import ProofTraceStore
from src.prover import Prover, ProverAction, ProverTargetTimeouts
from src.prover.file_controller import ProofFileController
from src.prover.models import ProverTarget
from src.prover.repl import ReplToolOrchestrator
from src.tools import ToolCall
from src.prover.tactics import should_decompose


def _packet(
    *,
    theorem_name: str,
    claim: str,
    lean_code: str,
    subgoals: list[dict[str, str]] | None = None,
    selected_preamble: list[str] | None = None,
    claim_type: str | None = None,
) -> FormalizationPacket:
    return FormalizationPacket.model_validate(
        {
            "claim": claim,
            "lean_code": lean_code,
            "theorem_with_sorry": lean_code,
            "theorem_name": theorem_name,
            "claim_type": claim_type,
            "imports": ["Mathlib"],
            "selected_imports": ["Mathlib"],
            "open_statements": [],
            "subgoals": subgoals or [],
            "selected_preamble": [] if selected_preamble is None else selected_preamble,
            "vacuity": {"is_vacuous": False},
            "faithfulness": FaithfulnessAssessment(
                score=5.0,
                coverage=1.0,
                structural_isomorphism=1.0,
                primitive_faithfulness=1.0,
                claim_frame={},
                stub_frame={},
                needs_human_review=False,
                passes_gate=True,
                feedback=[],
            ),
            "parse_check": ParseCheck(success=True, exit_code=0, stdout="", stderr=""),
            "review_state": "approved",
            "backend": "leanstral",
            "provider": "mistral",
            "model": "labs-leanstral-2603",
        }
    )


class ScriptedDriver:
    def __init__(self, scripts: dict[str, list[dict[str, object]]]) -> None:
        self.scripts = {name: list(entries) for name, entries in scripts.items()}
        self.call_count = 0

    def next_action(self, *, backend, prompt: str) -> ProverAction:
        self.call_count += 1
        payload = json.loads(prompt)
        target_name = str(payload["target"]["name"])
        if target_name not in self.scripts or not self.scripts[target_name]:
            raise AssertionError(f"No scripted action left for {target_name}")
        return ProverAction.model_validate(self.scripts[target_name].pop(0))


class FakeLeanError:
    def __init__(self, message: str) -> None:
        self.message = message


class FakeReplSession:
    def __init__(self, *, timeout: int | None = None) -> None:
        self.timeout = timeout
        self.code = ""
        self.theorem_name = ""
        self.tactics: list[str] = []

    def start_proof(self, theorem_with_sorry: str, timeout=None):
        self.code = theorem_with_sorry
        self.theorem_name = _theorem_name(theorem_with_sorry)
        self.tactics = []
        return SimpleNamespace(state_id=1, goals=[f"goal:{self.theorem_name}"], is_solved=False)

    def apply_tactic(self, tactic: str, timeout=None):
        if tactic == "bad_tactic":
            return FakeErrorResponse("unknown tactic `bad_tactic`")
        self.tactics.append(tactic)
        completed = tactic in {"ring", "norm_num", "trivial"}
        goals = [] if completed else [f"goal:{self.theorem_name}:after:{tactic}"]
        return SimpleNamespace(
            has_errors=lambda: False,
            goals=goals,
            proof_status="Completed" if completed else "InProgress",
            proof_state=len(self.tactics) + 1,
        )

    def materialize_proof(self):
        replacement = "\n".join(f"  {tactic}" for tactic in self.tactics) or "  sorry"
        return self.code.replace("  sorry", replacement, 1)

    def kill(self) -> None:
        return None


class FakeErrorResponse:
    def __init__(self, message: str) -> None:
        self._message = message

    def has_errors(self) -> bool:
        return True

    def get_errors(self):
        return [SimpleNamespace(data=self._message)]


class FakeLSPClient:
    def lean_diagnostic_messages(self, *args, **kwargs):
        return {"items": []}

    def lean_goal(self, *args, **kwargs):
        return {"goals_after": ["True"]}

    def lean_code_actions(self, *args, **kwargs):
        return {"items": []}

    def lean_hover_info(self, *args, **kwargs):
        return {"contents": "True"}

    def lean_leansearch(self, query: str, *, num_results: int = 8):
        return {"items": [{"name": "trivial_lemma"}]}

    def lean_loogle(self, query: str, *, num_results: int = 8):
        return {"items": []}

    def lean_local_search(self, query: str, *, limit: int = 8):
        return {"items": [{"name": query, "source": "local"}]}

    def lean_file_outline(self, file_path, *, max_declarations=None):
        return {"declarations": [{"name": "main_claim"}], "max_declarations": max_declarations}


def _theorem_name(code: str) -> str:
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("theorem "):
            return stripped.split()[1]
    return "anonymous"


def _fake_compile(code: str, **_: object) -> dict[str, object]:
    has_sorry = "sorry" in code
    success = False
    errors: list[str] = []
    if has_sorry:
        errors = ["Proof contains `sorry`."]
    elif "field_simp" in code and "ring" in code:
        success = True
    elif "norm_num" in code:
        success = True
    elif "trivial" in code:
        success = True
    elif "exact apollo_" in code:
        success = True
    elif "exact proved_" in code:
        success = True
    else:
        errors = ["unsolved proof"]
    return {
        "success": success,
        "has_sorry": has_sorry,
        "axiom_warnings": [],
        "output": "\n".join(errors),
        "errors": errors,
        "warnings": [],
        "stdout": "",
        "stderr": "\n".join(errors),
        "exit_code": 0 if success else 1,
    }


@pytest.mark.anyio
async def test_prover_benchmark_mode_uses_direct_definable_closure_for_preamble_hits(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def fake_compile(code: str, **_: object) -> dict[str, object]:
        success = "exact valueFunction_isFixedPt" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_value_function_fixed",
            claim="The value function selected from a contracting dynamic problem is fixed.",
            lean_code=(
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.ValueFunction\n\n"
                "theorem benchmark_value_function_fixed\n"
                "    {V : Type*} [MetricSpace V] [CompleteSpace V] [Nonempty V]\n"
                "    {K : NNReal} (T : V → V) (hT : ContractingWith K T) :\n"
                "    Function.IsFixedPt T (ValueFunction T hT) := by\n"
                "  sorry\n"
            ),
            selected_preamble=["value_function"],
        ),
        "job_direct_preamble_closure",
        benchmark_mode=True,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "exact valueFunction_isFixedPt" in result.verified_code
    assert any(step.action_type == "direct_definable_closure" for step in result.trace)


@pytest.mark.anyio
async def test_prover_reports_no_progress_stall_for_unchanged_repl_state(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    class NoProgressReplSession(FakeReplSession):
        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            return SimpleNamespace(
                has_errors=lambda: False,
                goals=[f"goal:{self.theorem_name}"],
                proof_status="InProgress",
                proof_state=len(self.tactics) + 1,
            )

        def materialize_proof(self):
            return self.code

    monkeypatch.setattr(prover_module, "LeanREPLSession", NoProgressReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Keep trying a tactic that does not move the proof state.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "simp"}},
                    }
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="no_progress_claim",
            claim="A claim whose REPL tactic leaves the proof state unchanged.",
            lean_code="import Mathlib\n\ntheorem no_progress_claim : True := by\n  sorry\n",
        ),
        "job_no_progress",
        max_turns=3,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.reason == "no_progress_stall"


@pytest.mark.anyio
async def test_prover_fails_fast_after_direct_closure_exhaustion_stalls(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    theorem_code = "import Mathlib\n\ntheorem stalled_after_shortcuts : True := by\n  sorry\n"

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(prover_module.Prover, "_has_direct_candidates", lambda self, **_: True)
    monkeypatch.setattr(
        prover_module.Prover,
        "_try_direct_definable_closure",
        lambda self, **_: (
            None,
            prover_module.DirectCloseAttemptSummary(
                candidate_count=4,
                attempt_limit=2,
                attempts_used=2,
                claim_type=None,
                claim_type_policy="default",
                preamble_shortcuts_enabled=True,
            ),
        ),
    )
    monkeypatch.setattr(
        prover_module.Prover,
        "_try_repl_compile_recovery",
        lambda self, **_: (
            None,
            prover_module.DirectCloseAttemptSummary(
                candidate_count=4,
                attempt_limit=2,
                attempts_used=2,
                claim_type=None,
                claim_type_policy="default",
                preamble_shortcuts_enabled=True,
            ),
        ),
    )
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    driver = ScriptedDriver(
        {
            "theorem_body": [
                {
                    "action_type": "tool",
                    "rationale": "Rewrite the same code back into the session.",
                    "tool": {"name": "write_current_code", "arguments": {"code": theorem_code}},
                },
                {
                    "action_type": "tool",
                    "rationale": "Try a tactic that still does not compile.",
                    "tool": {"name": "apply_tactic", "arguments": {"tactic": "bad_tactic"}},
                },
                {
                    "action_type": "tool",
                    "rationale": "The prover should not need this third turn.",
                    "tool": {"name": "apply_tactic", "arguments": {"tactic": "bad_tactic"}},
                },
            ]
        }
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=driver,
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="stalled_after_shortcuts",
            claim="A claim that should stop once shortcut recovery and tool turns stop changing state.",
            lean_code=theorem_code,
            selected_preamble=[],
        ),
        "job_stalled_after_shortcuts",
        max_turns=6,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.reason == "no_progress_stall"
    assert "Direct-close candidates were exhausted" in result.failure.message
    assert driver.call_count == 1


@pytest.mark.anyio
async def test_mathlib_native_claim_caps_direct_close_attempts_and_logs_awareness(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    attempt_counter = {"count": 0}

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module.Prover, "_selected_preamble_entries", lambda self, packet: [])
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    original_candidates = prover_module.Prover._direct_candidate_proofs
    original_try_direct = prover_module.Prover._try_direct_definable_closure

    def fake_candidates(self, *, packet, current_code, include_fallback_tactics=False):
        candidates = [
            (f"exact candidate_{index}", "mock", f"candidate {index}") for index in range(10)
        ]
        if include_fallback_tactics:
            return [
                *candidates,
                *original_candidates(
                    self, packet=packet, current_code=current_code, include_fallback_tactics=True
                ),
            ]
        return candidates

    def counting_try_direct(self, **kwargs):
        result, summary = original_try_direct(self, **kwargs)
        attempt_counter["count"] += summary.attempts_used
        return result, summary

    monkeypatch.setattr(prover_module.Prover, "_direct_candidate_proofs", fake_candidates)
    monkeypatch.setattr(prover_module.Prover, "_try_direct_definable_closure", counting_try_direct)

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Attempt a tactic after direct-close budget is exhausted.",
                        "tool": {
                            "name": "write_current_code",
                            "arguments": {
                                "code": "import Mathlib\n\ntheorem mathlib_native_budget : True := by\n  sorry\n"
                            },
                        },
                    }
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="mathlib_native_budget",
            claim="A mathlib-native claim should avoid spending many direct-close attempts.",
            lean_code="import Mathlib\n\ntheorem mathlib_native_budget : True := by\n  sorry\n",
            selected_preamble=["value_function"],
            claim_type="mathlib_native",
        ),
        "job_mathlib_native_budget",
        max_turns=3,
    )

    awareness_steps = [step for step in result.trace if step.action_type == "claim_type_awareness"]
    assert awareness_steps
    assert awareness_steps[0].tool_arguments["claim_type"] == "mathlib_native"
    assert awareness_steps[0].tool_arguments["direct_close_attempt_cap"] == 2
    assert awareness_steps[0].tool_arguments["preamble_shortcuts_enabled"] is False
    assert awareness_steps[0].tool_arguments["mathlib_native_mode"] is True
    assert "claim_type = mathlib_native" in awareness_steps[0].tool_result
    assert "skipping preamble-derived direct-close candidates" in awareness_steps[0].tool_result
    assert attempt_counter["count"] <= 2
    assert result.status in {"verified", "failed"}


@pytest.mark.anyio
async def test_claim_type_awareness_logs_for_subgoals_and_theorem_body(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "h_sub": [
                    {
                        "action_type": "tool",
                        "rationale": "Close the extracted subgoal directly.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    }
                ],
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Close the main theorem once the helper is available.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    }
                ],
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="claim_type_subgoal_logging",
            claim="Claim-type awareness should be visible on subgoals and the theorem body.",
            lean_code=(
                "import Mathlib\n\n"
                "theorem claim_type_subgoal_logging : True := by\n"
                "  have h_sub : True := by\n"
                "    sorry\n"
                "  exact h_sub\n"
            ),
            subgoals=[{"name": "h_sub", "statement": "True"}],
            claim_type="mathlib_native",
        ),
        "job_claim_type_subgoal_logging",
    )

    assert result.status == "verified"
    awareness_targets = {
        step.target_name for step in result.trace if step.action_type == "claim_type_awareness"
    }
    assert {"h_sub", "theorem_body"} <= awareness_targets


def test_mathlib_native_lsp_search_records_subgoal_context_and_tooling(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    def compile_when_candidate_closes(code: str, **_: object) -> dict[str, object]:
        success = "exact trivial_lemma" in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", compile_when_candidate_closes)

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FakeLSPClient(),
    )
    packet = _packet(
        theorem_name="main_claim",
        claim="A mathlib-native subgoal should expose LSP-assisted search context.",
        lean_code="import Mathlib\n\ntheorem main_claim : True := by\n  sorry\n",
        claim_type="mathlib_native",
    )
    target = ProverTarget(
        name="h_sub", statement="True", kind="subgoal", helper_theorem_name="h_sub"
    )
    session = SimpleNamespace(
        proof_path=tmp_path / "h_sub.lean",
        read_code=lambda: "import Mathlib\n\ntheorem h_sub : True := by\n  sorry\n",
    )
    trace = []
    audit_events = []
    progress_events = []

    closed_code = prover._try_mathlib_native_lsp_search(
        packet=packet,
        target=target,
        session=session,
        trace=trace,
        audit_events=audit_events,
        backend=prover.primary_backend,
        turn=1,
        timeout=30,
        lean_feedback=[],
        goals=["True"],
        job_id="job_lsp_subgoal",
        on_progress=lambda event, payload: progress_events.append({**payload, "event": event}),
    )
    prover._enrich_trace_context(packet=packet, targets=[target], trace=trace)

    assert closed_code is not None
    assert prover.budget_tracker.snapshot()["lsp_tool_calls"] == 5
    assert prover.budget_tracker.snapshot()["native_search_attempts"] == 2
    assert trace[0].target_name == "h_sub"
    assert trace[0].target_kind == "subgoal"
    assert trace[0].claim_type == "mathlib_native"
    assert trace[0].mathlib_native_mode is True
    assert trace[0].lsp_tool_call is True
    assert trace[0].native_search_attempt is True
    assert trace[0].tool_arguments["target_kind"] == "subgoal"
    assert any(
        event["metadata"].get("lsp_tool_name") == "lean_leansearch" for event in progress_events
    )


@pytest.mark.anyio
async def test_prover_detects_apply_get_goals_shallow_loop(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    class LoopingReplSession(FakeReplSession):
        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            return SimpleNamespace(
                has_errors=lambda: False,
                goals=[f"goal:{self.theorem_name}"],
                proof_status="InProgress",
                proof_state=len(self.tactics) + 1,
            )

    monkeypatch.setattr(prover_module, "LeanREPLSession", LoopingReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module.Prover,
        "_apply_deterministic_repair",
        lambda self, **kwargs: (False, None, None),
    )
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Try a tactic that keeps the same goal active.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "simp"}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Read goals without changing the proof state.",
                        "tool": {"name": "get_goals", "arguments": {}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Repeat the same no-op tactic cycle.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "simp"}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Confirm the goal still has not moved.",
                        "tool": {"name": "get_goals", "arguments": {}},
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="apply_get_goals_loop",
            claim="A claim that gets stuck in an apply_tactic and get_goals loop.",
            lean_code="import Mathlib\n\ntheorem apply_get_goals_loop : True := by\n  sorry\n",
        ),
        "job_apply_get_goals_loop",
        max_turns=6,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.reason == "no_progress_stall"
    assert "apply_tactic` -> `get_goals`" in result.failure.message


@pytest.mark.anyio
async def test_prover_detects_write_compile_shallow_loop(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    def _compile_missing_proof(code: str, **_: object) -> dict[str, object]:
        if "missing_proof" in code:
            errors = ["unknown constant `missing_proof`"]
            return {
                "success": False,
                "has_sorry": False,
                "axiom_warnings": [],
                "output": "\n".join(errors),
                "errors": errors,
                "warnings": [],
                "stdout": "",
                "stderr": "\n".join(errors),
                "exit_code": 1,
            }
        return _fake_compile(code)

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _compile_missing_proof)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module.Prover,
        "_apply_deterministic_repair",
        lambda self, **kwargs: (False, None, None),
    )
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Write one broken proof body.",
                        "tool": {
                            "name": "write_current_code",
                            "arguments": {
                                "code": "import Mathlib\n\ntheorem write_compile_loop : True := by\n  exact missing_proof\n",
                            },
                        },
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Compile the broken proof body.",
                        "tool": {"name": "compile_current_code", "arguments": {}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Rewrite the proof with the same semantic mistake.",
                        "tool": {
                            "name": "write_current_code",
                            "arguments": {
                                "code": "import Mathlib\n\ntheorem write_compile_loop : True := by\n  exact (missing_proof)\n",
                            },
                        },
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Compile the same failure again.",
                        "tool": {"name": "compile_current_code", "arguments": {}},
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="write_compile_loop",
            claim="A claim that gets stuck rewriting the same compile failure.",
            lean_code="import Mathlib\n\ntheorem write_compile_loop : True := by\n  sorry\n",
        ),
        "job_write_compile_loop",
        max_turns=6,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.reason == "no_progress_stall"
    assert "write_current_code` -> `compile_current_code`" in result.failure.message


@pytest.mark.anyio
async def test_prover_closes_simple_algebra_with_field_simp_and_ring(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def _driver_only_compile(code: str, **_: object) -> dict[str, object]:
        has_sorry = "sorry" in code
        if has_sorry:
            return {
                "success": False,
                "has_sorry": True,
                "axiom_warnings": [],
                "output": "Proof contains `sorry`.",
                "errors": ["Proof contains `sorry`."],
                "warnings": [],
                "stdout": "",
                "stderr": "Proof contains `sorry`.",
                "exit_code": 1,
            }
        success = "field_simp" in code and "ring" in code
        errors = [] if success else ["unsolved proof"]
        return {
            "success": success,
            "has_sorry": False,
            "axiom_warnings": [],
            "output": "\n".join(errors),
            "errors": errors,
            "warnings": [],
            "stdout": "",
            "stderr": "\n".join(errors),
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", _driver_only_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Clear denominators first.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "field_simp"}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Finish the normalized ring goal.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "ring"}},
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="field_ring_claim",
            claim="A simple algebraic claim closed by field_simp and ring.",
            lean_code="import Mathlib\n\ntheorem field_ring_claim : 1 / 1 = 1 := by\n  sorry\n",
        ),
        "job_field_ring",
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "field_simp" in result.verified_code
    assert "ring" in result.verified_code
    assert result.trace[-1].tool_arguments["tactic"] == "ring"


@pytest.mark.anyio
async def test_prover_self_corrects_after_lean_feedback(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    store = ProofTraceStore(tmp_path / "memory.db")
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Try a tactic that will fail.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "bad_tactic"}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Lean feedback says to normalize arithmetic directly.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "norm_num"}},
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=store,
    )

    result = await prover.prove(
        _packet(
            theorem_name="self_correct_claim",
            claim="A claim that requires one failed tactic before norm_num succeeds.",
            lean_code="import Mathlib\n\ntheorem self_correct_claim : 1 + 1 = 2 := by\n  sorry\n",
            selected_preamble=[],
        ),
        "job_self_correct",
    )

    assert result.status == "verified"
    assert len(result.trace) >= 2
    assert result.trace[0].success is False
    assert "unknown tactic" in result.trace[0].tool_result
    assert any(
        (
            step.tool_name == "apply_tactic"
            and (step.tool_arguments or {}).get("tactic") == "norm_num"
        )
        or (
            step.action_type == "direct_definable_closure"
            and (step.tool_arguments or {}).get("proof") == "norm_num"
        )
        for step in result.trace[1:]
    )

    recorded = store.list_recent(limit=1)[0]
    assert recorded.outcome == "verified"
    assert recorded.full_trace is not None
    assert recorded.lesson_summary is not None


@pytest.mark.anyio
async def test_prover_benchmark_mode_skips_memory_and_cleans_artifacts(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    workspace_root = tmp_path / "proofs"
    store = ProofTraceStore(tmp_path / "memory.db")
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Close the arithmetic goal directly.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "norm_num"}},
                    }
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=workspace_root),
        trace_store=store,
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_claim",
            claim="A benchmark proof that should not persist memory or artifacts.",
            lean_code="import Mathlib\n\ntheorem benchmark_claim : 1 + 1 = 2 := by\n  sorry\n",
        ),
        "job_benchmark_cleanup",
        target_timeouts=ProverTargetTimeouts(theorem_body=300, subgoal=180, apollo_lemma=120),
        benchmark_mode=True,
    )

    assert result.status == "verified"
    assert result.benchmark_mode is True
    assert result.verified_via == "full_pipeline"
    assert result.target_timeouts.model_dump(mode="json") == {
        "theorem_body": 300,
        "subgoal": 180,
        "apollo_lemma": 120,
    }
    assert store.list_recent(limit=1) == []
    assert not prover.file_controller.proof_path("job_benchmark_cleanup").exists()
    assert list((workspace_root / "checkpoints").glob("job_benchmark_cleanup_*.lean")) == []


def test_prover_resolves_per_target_timeouts_with_request_fallback(tmp_path) -> None:
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    resolved = prover._resolve_target_timeouts(
        timeout=300,
        target_timeouts=ProverTargetTimeouts(subgoal=180),
    )

    assert resolved.model_dump(mode="json") == {
        "theorem_body": 300,
        "subgoal": 180,
        "apollo_lemma": 300,
    }
    assert (
        prover._timeout_for_target(
            ProverTarget(name="main", statement="True", kind="theorem_body"),
            resolved,
        )
        == 300
    )
    assert (
        prover._timeout_for_target(
            ProverTarget(name="sub", statement="True", kind="subgoal"),
            resolved,
        )
        == 180
    )
    assert (
        prover._timeout_for_target(
            ProverTarget(name="lemma", statement="True", kind="apollo_lemma"),
            resolved,
        )
        == 300
    )
    assert prover._final_compile_timeout(resolved) == 300


def test_prover_recursion_depth_allows_three_and_rejects_four() -> None:
    action = ProverAction.model_validate(
        {
            "action_type": "decompose",
            "rationale": "Extract a helper lemma.",
            "decomposition_statement": "True",
        }
    )

    allowed = ProverTarget(
        name="depth_three", statement="True", kind="apollo_lemma", recursion_depth=3
    )
    assert allowed.recursion_depth == 3
    assert should_decompose(
        failed_turns_for_target=2,
        action=action,
        allow_decomposition=True,
        current_depth=2,
        total_extracted=0,
    )
    assert not should_decompose(
        failed_turns_for_target=2,
        action=action,
        allow_decomposition=True,
        current_depth=3,
        total_extracted=0,
    )
    assert should_decompose(
        failed_turns_for_target=2,
        action=action,
        allow_decomposition=True,
        current_depth=0,
        total_extracted=0,
        max_recursion_depth=1,
    )
    assert not should_decompose(
        failed_turns_for_target=2,
        action=action,
        allow_decomposition=True,
        current_depth=1,
        total_extracted=0,
        max_recursion_depth=1,
    )
    assert not should_decompose(
        failed_turns_for_target=2,
        action=None,
        allow_decomposition=True,
        current_depth=0,
        total_extracted=0,
        no_progress_streak=2,
        direct_candidates_available=True,
    )
    assert should_decompose(
        failed_turns_for_target=3,
        action=None,
        allow_decomposition=True,
        current_depth=0,
        total_extracted=0,
        no_progress_streak=3,
        direct_candidates_available=True,
    )

    with pytest.raises(ValidationError):
        ProverTarget(name="depth_four", statement="True", kind="apollo_lemma", recursion_depth=4)


@pytest.mark.anyio
async def test_prover_recovers_repl_compile_disagreement_with_compile_normalization(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def _compile_needs_norm_num(code: str, **_: object) -> dict[str, object]:
        has_sorry = "sorry" in code
        success = "norm_num" in code and not has_sorry
        errors = [] if success else ["unsolved proof"]
        return {
            "success": success,
            "has_sorry": has_sorry,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else errors,
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", _compile_needs_norm_num)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "The REPL thinks `trivial` closes the goal.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Retry the same locally-solved tactic.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="compile_recovery_claim",
            claim="A claim whose local REPL closure needs a cheap global normalization pass.",
            lean_code="import Mathlib\n\ntheorem compile_recovery_claim : 1 + 1 = 2 := by\n  sorry\n",
            selected_preamble=[],
        ),
        "job_compile_recovery",
        max_turns=4,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "norm_num" in result.verified_code
    assert any((step.tool_arguments or {}).get("proof") == "norm_num" for step in result.trace)


@pytest.mark.anyio
async def test_prover_uses_apollo_decomposition_for_stalled_target(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "decompose",
                        "rationale": "Extract the body as a helper lemma first.",
                        "decomposition_statement": "True",
                        "decomposition_name": "apollo_decompose_claim_1",
                    }
                ],
                "apollo_decompose_claim_1": [
                    {
                        "action_type": "tool",
                        "rationale": "The extracted helper closes with trivial.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    }
                ],
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="decompose_claim",
            claim="A claim that benefits from APOLLO-style decomposition.",
            lean_code="import Mathlib\n\ntheorem decompose_claim : True := by\n  sorry\n",
        ),
        "job_decompose",
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "apollo_decompose_claim_1" in result.verified_code
    assert any(step.action_type == "decompose" for step in result.trace)


@pytest.mark.anyio
async def test_prover_decomposition_rewrites_subgoal_after_repl_materialization(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "h_sub": [
                    {
                        "action_type": "tool",
                        "rationale": "Try a simple tactic before decomposing.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "simp"}},
                    },
                    {
                        "action_type": "decompose",
                        "rationale": "Extract a helper lemma once the direct path stalls.",
                        "decomposition_statement": "True",
                        "decomposition_name": "apollo_subgoal_claim_1",
                    },
                ],
                "apollo_subgoal_claim_1": [
                    {
                        "action_type": "tool",
                        "rationale": "The extracted helper closes with trivial.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    }
                ],
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="subgoal_claim",
            claim="A claim whose subgoal is decomposed after REPL materialization.",
            lean_code=(
                "import Mathlib\n\n"
                "theorem subgoal_claim : True := by\n"
                "  have h_sub : True := by\n"
                "    sorry\n"
                "  exact h_sub\n"
            ),
            subgoals=[{"name": "h_sub", "statement": "True"}],
        ),
        "job_subgoal_decompose",
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "exact apollo_subgoal_claim_1" in result.verified_code
    assert any(step.decomposition_theorem == "apollo_subgoal_claim_1" for step in result.trace)


@pytest.mark.anyio
async def test_prover_supports_lsp_tools_via_client(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    class FakeLSPClient:
        def lean_goal(self, file_path, *, line, column=None):
            return {"file": str(file_path), "line": line, "column": column, "goals": ["⊢ True"]}

        def lean_code_actions(self, file_path, *, line):
            return {"file": str(file_path), "line": line, "actions": ["exact?"]}

        def lean_hover_info(self, file_path, *, line, column):
            return {"file": str(file_path), "line": line, "column": column, "type": "True"}

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Inspect the live goal before closing it.",
                        "tool": {"name": "lean_goal", "arguments": {"line": 3, "column": 3}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "The goal is trivial after inspection.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FakeLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="lsp_claim",
            claim="A claim that first inspects goals through the LSP adapter.",
            lean_code="import Mathlib\n\ntheorem lsp_claim : True := by\n  sorry\n",
        ),
        "job_lsp",
    )

    assert result.status == "verified"
    assert result.trace[0].tool_name == "lean_goal"
    assert '"goals": ["\\u22a2 True"]' in result.trace[0].tool_result
    assert result.usage_by_stage["prover"]["stage"] == "prover"


@pytest.mark.anyio
async def test_mathlib_native_lsp_search_closes_with_compile_validated_code_action(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)

    compile_attempts: list[str] = []

    def fake_compile(code: str, **_: object) -> dict[str, object]:
        compile_attempts.append(code)
        success = "trivial" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    class FakeLSPClient:
        def lean_diagnostic_messages(self, file_path, **kwargs):
            return {"diagnostics": []}

        def lean_goal(self, file_path, *, line, column=None):
            return {"goals": ["⊢ True"]}

        def lean_code_actions(self, file_path, *, line):
            return {"actions": [{"title": "Try this: trivial"}]}

        def lean_hover_info(self, file_path, *, line, column):
            return {"type": "True"}

        def lean_leansearch(self, query, *, num_results=8):
            return {"items": [{"name": "True.intro"}]}

    progress_events: list[dict[str, object]] = []
    driver = ScriptedDriver({})
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FakeLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="mathlib_lsp_code_action",
            claim="A mathlib-native claim should use LSP code actions before provider turns.",
            lean_code="import Mathlib\n\ntheorem mathlib_lsp_code_action : True := by\n  sorry\n",
            claim_type="mathlib_native",
        ),
        "job_mathlib_lsp_code_action",
        max_turns=3,
        on_progress=lambda _event, payload: progress_events.append(payload),
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "trivial" in result.verified_code
    assert driver.call_count == 0
    search_steps = [
        step for step in result.trace if step.action_type == "mathlib_native_lsp_search"
    ]
    assert search_steps
    assert search_steps[-1].success is True
    assert search_steps[-1].tool_arguments["mathlib_native_mode"] is True
    assert search_steps[-1].tool_arguments["candidate_count"] >= 1
    assert compile_attempts
    assert any(
        event.get("event") == "prover_tool"
        and (event.get("metadata") or {}).get("lsp_tool_name") == "lean_leansearch"
        for event in progress_events
    )


@pytest.mark.anyio
async def test_preamble_definable_claim_does_not_run_mathlib_native_lsp_search(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    class FailingLSPClient:
        def __getattr__(self, name):
            raise AssertionError(f"LSP should not be called for preamble-definable claims: {name}")

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Close without mathlib-native search.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                    }
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FailingLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="preamble_no_lsp_search",
            claim="A preamble-definable claim should keep the existing prover path.",
            lean_code="import Mathlib\n\ntheorem preamble_no_lsp_search : True := by\n  sorry\n",
            claim_type="preamble_definable",
        ),
        "job_preamble_no_lsp_search",
        max_turns=3,
    )

    assert result.status == "verified"
    assert not any(step.action_type == "mathlib_native_lsp_search" for step in result.trace)


@pytest.mark.anyio
async def test_mathlib_native_lsp_unavailable_falls_back_to_provider(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    class UnavailableLSPClient:
        def lean_diagnostic_messages(self, *args, **kwargs):
            raise prover_module.LeanLSPUnavailableError("server unavailable")

        lean_goal = lean_diagnostic_messages
        lean_code_actions = lean_diagnostic_messages
        lean_hover_info = lean_diagnostic_messages
        lean_leansearch = lean_diagnostic_messages

    driver = ScriptedDriver(
        {
            "theorem_body": [
                {
                    "action_type": "tool",
                    "rationale": "Fallback provider closes the goal.",
                    "tool": {"name": "apply_tactic", "arguments": {"tactic": "trivial"}},
                }
            ]
        }
    )
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=UnavailableLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="mathlib_lsp_fallback",
            claim="A mathlib-native claim should fall back when LSP is unavailable.",
            lean_code="import Mathlib\n\ntheorem mathlib_lsp_fallback : True := by\n  sorry\n",
            claim_type="mathlib_native",
        ),
        "job_mathlib_lsp_fallback",
        max_turns=3,
    )

    assert result.status == "verified"
    assert driver.call_count == 1
    failed_search_steps = [
        step
        for step in result.trace
        if step.action_type == "mathlib_native_lsp_search" and not step.success
    ]
    harness_steps = [
        step for step in result.trace if step.action_type == "mathlib_native_harness_loop"
    ]
    assert failed_search_steps or harness_steps
    if failed_search_steps:
        assert failed_search_steps[-1].error_code == "lsp_unavailable"
    else:
        assert harness_steps[-1].tool_arguments["RetrievalEvent"]["retrieved_count"] >= 1


@pytest.mark.anyio
async def test_mathlib_native_lsp_search_compile_validates_before_accepting(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)

    compile_attempts: list[str] = []

    def fake_compile(code: str, **_: object) -> dict[str, object]:
        compile_attempts.append(code)
        success = "exact GoodLemma" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unknown constant",
            "errors": [] if success else ["unknown constant"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unknown constant",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    class FakeLSPClient:
        def lean_diagnostic_messages(self, file_path, **kwargs):
            return {"diagnostics": []}

        def lean_goal(self, file_path, *, line, column=None):
            return {"goals": ["⊢ True"]}

        def lean_code_actions(self, file_path, *, line):
            return {"actions": [{"title": "Try this: exact BadLemma"}]}

        def lean_hover_info(self, file_path, *, line, column):
            return {"type": "True"}

        def lean_leansearch(self, query, *, num_results=8):
            return {"items": [{"name": "GoodLemma"}]}

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FakeLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="mathlib_lsp_compile_validated",
            claim="A mathlib-native claim should reject failed LSP suggestions before accepting a later one.",
            lean_code="import Mathlib\n\ntheorem mathlib_lsp_compile_validated : True := by\n  sorry\n",
            claim_type="mathlib_native",
        ),
        "job_mathlib_lsp_compile_validated",
        max_turns=3,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "exact GoodLemma" in result.verified_code
    assert any("exact BadLemma" in attempt for attempt in compile_attempts)
    assert any("exact GoodLemma" in attempt for attempt in compile_attempts)
    final_search_step = [
        step for step in result.trace if step.action_type == "mathlib_native_lsp_search"
    ][-1]
    assert final_search_step.tool_arguments["compiled_candidate_count"] == 2
    assert final_search_step.tool_arguments["selected_lemma"] == "GoodLemma"


@pytest.mark.anyio
async def test_prover_uses_trivial_shortcut_when_goal_matches_hypothesis(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def fake_compile(code: str, **_: object) -> dict[str, object]:
        success = "exact hspend" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    huggingface_driver = ScriptedDriver({})
    mistral_driver = ScriptedDriver({})
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=huggingface_driver,
        mistral_driver=mistral_driver,
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    lean_code = (
        "import Mathlib\n\n"
        "theorem benchmark_budget_constraint\n"
        "    (m p1 p2 x1 x2 : \u211d)\n"
        "    (hm : m > 0) (hp1 : p1 > 0) (hp2 : p2 > 0)\n"
        "    (hspend : p1 * x1 + p2 * x2 = m) :\n"
        "    p1 * x1 + p2 * x2 = m := by\n"
        "  sorry\n"
    )
    result = await prover.prove(
        _packet(
            theorem_name="benchmark_budget_constraint",
            claim="Goal literally matches the `hspend` hypothesis.",
            lean_code=lean_code,
        ),
        "job_trivial_shortcut",
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "exact hspend" in result.verified_code
    assert result.verified_via == "trivial_shortcut"
    assert any(step.action_type == "trivial_shortcut" for step in result.trace)
    assert all(step.action_type == "trivial_shortcut" for step in result.trace)


@pytest.mark.anyio
async def test_prover_shortcut_falls_back_to_exact_question_mark(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def fake_compile(code: str, **_: object) -> dict[str, object]:
        success = "exact?" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", fake_compile)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    lean_code = (
        "import Mathlib\n\n"
        "theorem benchmark_measure_empty {\u03b1 : Type*} [MeasurableSpace \u03b1]\n"
        "    (\u03bc : MeasureTheory.Measure \u03b1) :\n"
        "    \u03bc \u2205 = 0 := by\n"
        "  sorry\n"
    )
    result = await prover.prove(
        _packet(
            theorem_name="benchmark_measure_empty",
            claim="Empty event has zero measure.",
            lean_code=lean_code,
        ),
        "job_exact_question_mark",
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "exact?" in result.verified_code
    shortcut_steps = [step for step in result.trace if step.action_type == "trivial_shortcut"]
    assert shortcut_steps
    assert "exact?" in shortcut_steps[0].tool_result


@pytest.mark.anyio
async def test_prover_soft_repairs_repl_compile_disagreement(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    class LoopReplSession(FakeReplSession):
        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            return SimpleNamespace(
                has_errors=lambda: False,
                goals=[],
                proof_status="Completed",
                proof_state=len(self.tactics) + 1,
            )

        def materialize_proof(self):
            return "theorem disagreement_claim : True := by\n  sorry\n"

    def fake_compile(code: str, **_: object) -> dict[str, object]:
        success = "import Mathlib" in code and "exact True.intro" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "LeanREPLSession", LoopReplSession)
    monkeypatch.setattr(prover_module, "compile_check", fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "First exact attempt closes the local REPL goal.",
                        "tool": {
                            "name": "apply_tactic",
                            "arguments": {"tactic": "exact True.intro"},
                        },
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Repeat the same exact proof so the prover triggers a soft repair.",
                        "tool": {
                            "name": "apply_tactic",
                            "arguments": {"tactic": "exact True.intro"},
                        },
                    },
                ]
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="disagreement_claim",
            claim="REPL reports solved, then succeeds after rebuilding theorem context once.",
            lean_code="import Mathlib\n\ntheorem disagreement_claim : True := by\n  sorry\n",
        ),
        "job_repl_compile_disagreement",
        max_turns=5,
    )

    assert result.status == "verified"
    assert result.failure is None
    assert result.verified_code is not None
    assert result.verified_code.startswith("import Mathlib")
    assert "exact True.intro" in result.verified_code
    assert any(step.action_type == "repl_compile_soft_repair" for step in result.trace)
    assert any(step.repl_local_solved for step in result.trace)


@pytest.mark.anyio
async def test_prover_preserves_max_turns_exhausted_error_code(tmp_path, monkeypatch) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver(
            {
                "theorem_body": [
                    {
                        "action_type": "tool",
                        "rationale": "Keep trying a tactic that never closes the goal.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "simp"}},
                    }
                ]
                * 6,
            }
        ),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="max_turns_claim",
            claim="A claim that exhausts the turn budget.",
            lean_code="import Mathlib\n\ntheorem max_turns_claim : True := by\n  sorry\n",
            selected_preamble=[],
        ),
        "job_max_turns",
        max_turns=3,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.error_code == "max_turns_exhausted"
    assert result.termination_reason == "max_turns_exhausted"


@pytest.mark.anyio
async def test_prover_scaffolds_monotone_goal_before_metadata_branch_closure(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    class MonotoneScaffoldRepl(FakeReplSession):
        def start_proof(self, theorem_with_sorry: str, timeout=None):
            self.code = theorem_with_sorry
            self.theorem_name = _theorem_name(theorem_with_sorry)
            self.tactics = []
            return SimpleNamespace(
                state_id=1,
                goals=["⊢ Monotone (BellmanOperator reward transition β)"],
                is_solved=False,
            )

        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            if tactic == "intro x y hxy":
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=[
                        "⊢ BellmanOperator reward transition β x ≤ BellmanOperator reward transition β y"
                    ],
                    proof_status="InProgress",
                    proof_state=len(self.tactics) + 1,
                )
            if tactic == "exact BellmanOperator.monotone hβ hxy":
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=[],
                    proof_status="Completed",
                    proof_state=len(self.tactics) + 1,
                )
            return FakeErrorResponse(f"unsupported tactic `{tactic}`")

    def _compile_monotone_scaffold(code: str, **_: object) -> dict[str, object]:
        success = (
            "intro x y hxy" in code
            and "exact BellmanOperator.monotone hβ hxy" in code
            and "sorry" not in code
        )
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "LeanREPLSession", MonotoneScaffoldRepl)
    monkeypatch.setattr(prover_module, "compile_check", _compile_monotone_scaffold)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    driver = ScriptedDriver({})
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_blackwell_order_preserving",
            claim="A Bellman-style operator is order preserving.",
            lean_code=(
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator\n\n"
                "theorem benchmark_blackwell_order_preserving\n"
                "    {S : Type*} {reward : S → ℝ} {transition : S → S} {β : ℝ}\n"
                "    (hβ : 0 ≤ β) :\n"
                "    Monotone (BellmanOperator reward transition β) := by\n"
                "  sorry\n"
            ),
            selected_preamble=["bellman_operator"],
        ),
        "job_monotone_scaffold",
        max_turns=4,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "intro x y hxy" in result.verified_code
    assert "exact BellmanOperator.monotone hβ hxy" in result.verified_code
    assert any(step.action_type == "deterministic_scaffold" for step in result.trace)
    assert any(step.action_type == "deterministic_branch_tactic" for step in result.trace)
    assert driver.call_count == 0


@pytest.mark.anyio
async def test_prover_scaffolds_conjunction_goal_and_closes_both_branches(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    class ConjunctionRepl(FakeReplSession):
        def start_proof(self, theorem_with_sorry: str, timeout=None):
            self.code = theorem_with_sorry
            self.theorem_name = _theorem_name(theorem_with_sorry)
            self.tactics = []
            self.branch = "root"
            return SimpleNamespace(
                state_id=1,
                goals=["⊢ ContinuousOn u feasible ∧ u y ≤ u x"],
                is_solved=False,
            )

        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            if tactic == "constructor":
                self.branch = "left"
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=["⊢ ContinuousOn u feasible", "⊢ u y ≤ u x"],
                    proof_status="InProgress",
                    proof_state=len(self.tactics) + 1,
                )
            if self.branch == "left" and tactic in {
                "exact hu.continuousOn",
                "exact continuousPreference_continuousOn hu feasible",
            }:
                self.branch = "right"
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=["⊢ u y ≤ u x"],
                    proof_status="InProgress",
                    proof_state=len(self.tactics) + 1,
                )
            if self.branch == "right" and tactic in {
                "exact hx.2 hy",
                "exact IsConstrainedMaximum.value_le hx hy",
            }:
                self.branch = "done"
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=[],
                    proof_status="Completed",
                    proof_state=len(self.tactics) + 1,
                )
            return FakeErrorResponse(f"unsupported tactic `{tactic}`")

    def _compile_conjunction(code: str, **_: object) -> dict[str, object]:
        success = (
            "constructor" in code
            and (
                "exact continuousPreference_continuousOn hu feasible" in code
                or "exact hu.continuousOn" in code
            )
            and ("exact IsConstrainedMaximum.value_le hx hy" in code or "exact hx.2 hy" in code)
            and "sorry" not in code
        )
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "LeanREPLSession", ConjunctionRepl)
    monkeypatch.setattr(prover_module, "compile_check", _compile_conjunction)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_continuous_argmax_certificate",
            claim="Continuous argmax certificates split into continuity and value branches.",
            lean_code=(
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.Preferences.ContinuousPreference\n"
                "import LeanEcon.Preamble.Foundations.Optimization.ConstrainedOptimization\n\n"
                "theorem benchmark_continuous_argmax_certificate\n"
                "    {α : Type*} [TopologicalSpace α] [TopologicalSpace ℝ]\n"
                "    {u : α → ℝ} {feasible : Set α} {x y : α}\n"
                "    (hu : ContinuousPreference u)\n"
                "    (hx : IsConstrainedMaximum u feasible x)\n"
                "    (hy : y ∈ feasible) :\n"
                "    ContinuousOn u feasible ∧ u y ≤ u x := by\n"
                "  sorry\n"
            ),
            selected_preamble=["continuous_preference", "constrained_optimization"],
        ),
        "job_conjunction_scaffold",
        max_turns=5,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "constructor" in result.verified_code
    assert (
        "exact continuousPreference_continuousOn hu feasible" in result.verified_code
        or "exact hu.continuousOn" in result.verified_code
    )
    assert (
        "exact IsConstrainedMaximum.value_le hx hy" in result.verified_code
        or "exact hx.2 hy" in result.verified_code
    )
    assert any(step.action_type == "deterministic_scaffold" for step in result.trace)
    assert sum(1 for step in result.trace if step.action_type == "deterministic_branch_tactic") >= 2


@pytest.mark.anyio
async def test_prover_prefers_existential_witness_scaffold_from_metadata(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    class ExistentialRepl(FakeReplSession):
        def start_proof(self, theorem_with_sorry: str, timeout=None):
            self.code = theorem_with_sorry
            self.theorem_name = _theorem_name(theorem_with_sorry)
            self.tactics = []
            return SimpleNamespace(
                state_id=1,
                goals=["⊢ ∃ x, Function.IsFixedPt f x"],
                is_solved=False,
            )

        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            if tactic == "refine ⟨ContractingWith.fixedPoint (f := f) hf, ?_⟩":
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=["⊢ Function.IsFixedPt f (ContractingWith.fixedPoint (f := f) hf)"],
                    proof_status="InProgress",
                    proof_state=len(self.tactics) + 1,
                )
            if tactic in {
                "exact ContractingWith.fixedPoint_isFixedPt (f := f) hf",
                "exact fixedPoint_isFixedPt hf",
            }:
                return SimpleNamespace(
                    has_errors=lambda: False,
                    goals=[],
                    proof_status="Completed",
                    proof_state=len(self.tactics) + 1,
                )
            return FakeErrorResponse(f"unsupported tactic `{tactic}`")

    def _compile_existential(code: str, **_: object) -> dict[str, object]:
        success = (
            "refine ⟨ContractingWith.fixedPoint (f := f) hf, ?_⟩" in code
            and (
                "exact ContractingWith.fixedPoint_isFixedPt (f := f) hf" in code
                or "exact fixedPoint_isFixedPt hf" in code
            )
            and "sorry" not in code
        )
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "LeanREPLSession", ExistentialRepl)
    monkeypatch.setattr(prover_module, "compile_check", _compile_existential)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_fixed_point_exists",
            claim="A contraction admits a fixed point.",
            lean_code=(
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.Equilibrium.FixedPointTheorem\n\n"
                "theorem benchmark_fixed_point_exists\n"
                "    {α : Type*} [MetricSpace α] [CompleteSpace α] [Nonempty α]\n"
                "    {K : NNReal} {f : α → α} (hf : ContractingWith K f) :\n"
                "    ∃ x, Function.IsFixedPt f x := by\n"
                "  sorry\n"
            ),
            selected_preamble=["fixed_point_theorem"],
        ),
        "job_existential_scaffold",
        max_turns=4,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "refine ⟨ContractingWith.fixedPoint (f := f) hf, ?_⟩" in result.verified_code
    assert "exact ContractingWith.fixedPoint_isFixedPt (f := f) hf" in result.verified_code
    scaffold_steps = [step for step in result.trace if step.action_type == "deterministic_scaffold"]
    assert scaffold_steps
    assert (
        scaffold_steps[0].tool_arguments["tactic"]
        == "refine ⟨ContractingWith.fixedPoint (f := f) hf, ?_⟩"
    )


@pytest.mark.anyio
async def test_prover_wrapper_aware_direct_closure_uses_simpa_on_known_lemma(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def _compile_wrapper_direct(code: str, **_: object) -> dict[str, object]:
        success = (
            "simpa [Monotone] using BellmanOperator.monotone hβ" in code and "sorry" not in code
        )
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", _compile_wrapper_direct)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    driver = ScriptedDriver({})
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_blackwell_order_preserving",
            claim="A Bellman-style operator is order preserving.",
            lean_code=(
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator\n\n"
                "theorem benchmark_blackwell_order_preserving\n"
                "    {S : Type*} {reward : S → ℝ} {transition : S → S} {β : ℝ}\n"
                "    (hβ : 0 ≤ β) :\n"
                "    Monotone (BellmanOperator reward transition β) := by\n"
                "  sorry\n"
            ),
            selected_preamble=["bellman_operator"],
        ),
        "job_wrapper_direct",
        max_turns=3,
        benchmark_mode=True,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "simpa [Monotone] using BellmanOperator.monotone hβ" in result.verified_code
    assert any(step.action_type == "direct_definable_closure" for step in result.trace)
    assert driver.call_count == 0


@pytest.mark.anyio
async def test_prover_statewise_direct_closure_prefers_fully_applied_preamble_lemma(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    compile_attempts: list[str] = []

    def _compile_statewise_direct(code: str, **_: object) -> dict[str, object]:
        compile_attempts.append(code)
        success = "exact BellmanOperator.monotone hβ hvw s" in code and "sorry" not in code
        return {
            "success": success,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "" if success else "unsolved proof",
            "errors": [] if success else ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "" if success else "unsolved proof",
            "exit_code": 0 if success else 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", _compile_statewise_direct)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    driver = ScriptedDriver({})
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="benchmark_bellman_operator_statewise",
            claim="Bellman monotonicity can be read pointwise at any fixed state.",
            lean_code=(
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator\n\n"
                "theorem benchmark_bellman_operator_statewise\n"
                "    {S : Type*} {reward : S → ℝ} {transition : S → S} {β : ℝ}\n"
                "    (hβ : 0 ≤ β) {v w : S → ℝ} (hvw : ∀ s, v s ≤ w s) (s : S) :\n"
                "    BellmanOperator reward transition β v s ≤\n"
                "      BellmanOperator reward transition β w s := by\n"
                "  sorry\n"
            ),
            selected_preamble=["bellman_operator"],
        ),
        "job_statewise_direct",
        max_turns=3,
        benchmark_mode=True,
    )

    assert result.status == "verified"
    assert result.verified_code is not None
    assert "exact BellmanOperator.monotone hβ hvw s" in result.verified_code
    assert compile_attempts
    assert "exact BellmanOperator.monotone hβ hvw s" in compile_attempts[0]
    assert len(compile_attempts) == 2
    assert any(step.action_type == "direct_definable_closure" for step in result.trace)
    assert driver.call_count == 0


@pytest.mark.anyio
async def test_prover_fails_fast_after_repeated_schema_invalid_actions(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)

    def _always_fail_compile(code: str, **_: object) -> dict[str, object]:
        return {
            "success": False,
            "has_sorry": "sorry" in code,
            "axiom_warnings": [],
            "output": "unsolved proof",
            "errors": ["unsolved proof"],
            "warnings": [],
            "stdout": "",
            "stderr": "unsolved proof",
            "exit_code": 1,
        }

    monkeypatch.setattr(prover_module, "compile_check", _always_fail_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module,
        "lean_run_code",
        lambda code, **kwargs: {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
    )

    driver = ScriptedDriver(
        {
            "theorem_body": [
                {
                    "action_type": "tool",
                    "rationale": "Invalid tool action.",
                    "tool": {"name": "bad_tool", "arguments": {}},
                },
                {
                    "action_type": "tool",
                    "rationale": "Repeat the same invalid action.",
                    "tool": {"name": "bad_tool", "arguments": {}},
                },
                {
                    "action_type": "tool",
                    "rationale": "Repeat again; prover should stop here.",
                    "tool": {"name": "bad_tool", "arguments": {}},
                },
            ]
        }
    )
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=driver,
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
    )

    result = await prover.prove(
        _packet(
            theorem_name="schema_invalid_repeat_claim",
            claim="Repeated invalid provider actions should fail fast.",
            lean_code="import Mathlib\n\ntheorem schema_invalid_repeat_claim : True := by\n  sorry\n",
            selected_preamble=[],
        ),
        "job_schema_invalid_repeat",
        max_turns=6,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.reason in {"no_progress_stall", "max_turns_exhausted"}
    assert driver.call_count < 6


class PromptCapturingDriver:
    def __init__(self, tactic: str) -> None:
        self.tactic = tactic
        self.prompts: list[str] = []
        self.call_count = 0

    def next_action(self, *, backend, prompt: str) -> ProverAction:
        self.call_count += 1
        self.prompts.append(prompt)
        return ProverAction.model_validate(
            {
                "action_type": "tool",
                "rationale": "Use the retrieved premise context to close the goal.",
                "tool": {"name": "apply_tactic", "arguments": {"tactic": self.tactic}},
            }
        )


def _mathlib_summary(prover_module):
    return prover_module.DirectCloseAttemptSummary(
        candidate_count=0,
        attempt_limit=2,
        attempts_used=0,
        claim_type="mathlib_native",
        claim_type_policy="mathlib_native_cap_2_no_preamble_shortcuts",
        preamble_shortcuts_enabled=False,
    )


@pytest.mark.anyio
async def test_mathlib_native_harness_loop_uses_retrieved_premises_in_prompt(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module
    import src.retrieval.mathlib_rag as mathlib_rag

    monkeypatch.setattr(prover_module, "LeanREPLSession", FakeReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module.Prover,
        "_try_direct_definable_closure",
        lambda self, **kwargs: (None, _mathlib_summary(prover_module)),
    )
    monkeypatch.setattr(
        prover_module.Prover,
        "_apply_deterministic_repair",
        lambda self, **kwargs: (False, None, None),
    )
    monkeypatch.setattr(
        mathlib_rag,
        "retrieve_premises",
        lambda goal_state, k=5: [
            {
                "name": "True.intro",
                "statement": "True",
                "score": 0.91,
                "file_path": "Mathlib/Init/Logic.lean",
            }
        ],
    )

    driver = PromptCapturingDriver("trivial")
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FakeLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="mathlib_harness_retrieval",
            claim="A mathlib-native claim should retrieve premises before proposing tactics.",
            lean_code="import Mathlib\n\ntheorem mathlib_harness_retrieval : True := by\n  sorry\n",
            claim_type="mathlib_native",
        ),
        "job_mathlib_harness_retrieval",
        max_turns=3,
        benchmark_mode=True,
    )

    assert result.status == "verified"
    assert driver.call_count == 1
    assert "True.intro" in driver.prompts[0]
    harness_steps = [
        step for step in result.trace if step.action_type == "mathlib_native_harness_loop"
    ]
    assert harness_steps
    payload = harness_steps[-1].tool_arguments
    assert payload["RetrievalEvent"]["retrieved_count"] == 1
    assert payload["ToolUsageTrace"]["state_hash_before"]
    assert payload["ToolUsageTrace"]["state_hash_after"]
    assert payload["ProgressDelta"]["goals_reduced"] is True


@pytest.mark.anyio
async def test_mathlib_native_harness_stall_uses_progress_delta_not_no_progress_reason(
    tmp_path, monkeypatch
) -> None:
    import src.prover.prover as prover_module
    import src.retrieval.mathlib_rag as mathlib_rag

    class NoProgressReplSession(FakeReplSession):
        def apply_tactic(self, tactic: str, timeout=None):
            self.tactics.append(tactic)
            return SimpleNamespace(
                has_errors=lambda: False,
                goals=[f"goal:{self.theorem_name}"],
                proof_status="InProgress",
                proof_state=len(self.tactics) + 1,
            )

        def materialize_proof(self):
            return self.code

    monkeypatch.setattr(prover_module, "LeanREPLSession", NoProgressReplSession)
    monkeypatch.setattr(prover_module, "compile_check", _fake_compile)
    monkeypatch.setattr(prover_module.Prover, "_try_trivial_shortcut", lambda self, **_: None)
    monkeypatch.setattr(
        prover_module.Prover,
        "_try_direct_definable_closure",
        lambda self, **kwargs: (None, _mathlib_summary(prover_module)),
    )
    monkeypatch.setattr(
        prover_module.Prover,
        "_apply_deterministic_repair",
        lambda self, **kwargs: (False, None, None),
    )
    monkeypatch.setattr(
        mathlib_rag,
        "retrieve_premises",
        lambda goal_state, k=5: [{"name": "SomeLemma", "statement": goal_state, "score": 0.8}],
    )

    driver = PromptCapturingDriver("simp")
    prover = Prover(
        backend="goedel-prover-v2",
        huggingface_driver=driver,
        mistral_driver=ScriptedDriver({}),
        file_controller=ProofFileController(workspace_root=tmp_path),
        trace_store=ProofTraceStore(tmp_path / "memory.db"),
        lsp_client=FakeLSPClient(),
    )

    result = await prover.prove(
        _packet(
            theorem_name="mathlib_harness_stall",
            claim="A mathlib-native harness turn should report observable stall deltas.",
            lean_code="import Mathlib\n\ntheorem mathlib_harness_stall : True := by\n  sorry\n",
            claim_type="mathlib_native",
        ),
        "job_mathlib_harness_stall",
        max_turns=3,
        benchmark_mode=True,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.reason == "progress_stall"
    assert result.termination_reason != "no_progress_stall"
    harness_steps = [
        step for step in result.trace if step.action_type == "mathlib_native_harness_loop"
    ]
    assert harness_steps[-1].tool_arguments["ProgressDelta"]["stall_detected"] is True


@pytest.mark.anyio
async def test_repl_orchestrator_supports_priority_lsp_tools(tmp_path) -> None:
    proof_path = tmp_path / "priority_tools.lean"
    proof_path.write_text(
        "import Mathlib\n\ntheorem priority_tools : True := by\n  sorry\n", encoding="utf-8"
    )
    verification_trace: dict[str, object] = {}
    orchestrator = ReplToolOrchestrator(
        repl=FakeReplSession(),
        theorem_code=proof_path.read_text(encoding="utf-8"),
        file_controller=None,
        job_id="job_priority_tools",
        budget_tracker=SimpleNamespace(record_sub_agent_call=lambda _name: None),
        telemetry=None,
        theorem_name="priority_tools",
        attempts=[],
        verification_trace=verification_trace,
        timed_compile_check=lambda *args, **kwargs: {"success": True},
        sync_classified_errors=lambda *args, **kwargs: None,
        build_status=lambda *args, **kwargs: {},
        completed_status=lambda *args, **kwargs: {},
        goal_analyst_hint_fn=lambda **kwargs: None,
        lsp_client=FakeLSPClient(),
        proof_path=proof_path,
    )
    await orchestrator.initialize()

    local = orchestrator.handle_tool_call(
        ToolCall(id="local", name="lean_local_search", arguments={"query": "True.intro"}),
        read_without_act=0,
    )
    outline = orchestrator.handle_tool_call(
        ToolCall(id="outline", name="lean_file_outline", arguments={"max_declarations": 5}),
        read_without_act=0,
    )
    actions = orchestrator.handle_tool_call(
        ToolCall(id="actions", name="code_actions", arguments={"line": 3}),
        read_without_act=0,
    )

    assert local.is_error is False
    assert "True.intro" in local.content
    assert outline.is_error is False
    assert "declarations" in outline.content
    assert actions.is_error is False


# ----- MistralProverDriver retry behaviour (Sprint 21) -----


def _mistral_payload(
    content: str = '{"action_type":"finish","finish_reason":"done"}',
) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


class _StubResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _http_error(code: int, message: bytes = b""):
    import io
    import urllib.error as urllib_error

    return urllib_error.HTTPError(
        url="https://api.mistral.ai/v1/chat/completions",
        code=code,
        msg=str(code),
        hdrs=None,
        fp=io.BytesIO(message),
    )


def test_mistral_prover_driver_retries_on_429_then_succeeds(monkeypatch) -> None:
    import urllib.request

    from src.prover.prover import MistralProverDriver, ProverBackend

    driver = MistralProverDriver(
        api_key="test-key", base_url="https://api.mistral.ai/v1", timeout=1.0
    )
    backend = ProverBackend(
        name="leanstral", provider="mistral", model="labs-leanstral", notes="test"
    )

    sleeps: list[float] = []
    monkeypatch.setattr("src.prover.prover.time.sleep", lambda seconds: sleeps.append(seconds))

    calls: list[int] = []

    def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, b'{"error":"rate_limited"}')
        return _StubResponse(_mistral_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = driver.next_action(backend=backend, prompt="goal")
    assert isinstance(result, tuple)
    action, metadata = result

    assert action.action_type == "finish"
    assert action.finish_reason == "done"
    assert len(calls) == 2  # one 429 + one success
    assert sleeps == [0.5]
    assert metadata is not None
    assert metadata.usage_source == "provider"


def test_mistral_prover_driver_exhausts_attempts_on_503(monkeypatch) -> None:
    import urllib.request

    from src.prover.prover import (
        MistralProverDriver,
        ProverBackend,
        ProverDriverError,
        PROVER_RETRY_ATTEMPTS,
    )

    driver = MistralProverDriver(
        api_key="test-key", base_url="https://api.mistral.ai/v1", timeout=1.0
    )
    backend = ProverBackend(
        name="leanstral", provider="mistral", model="labs-leanstral", notes="test"
    )

    sleeps: list[float] = []
    monkeypatch.setattr("src.prover.prover.time.sleep", lambda seconds: sleeps.append(seconds))

    calls: list[int] = []

    def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise _http_error(503, b'{"error":"service_unavailable"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProverDriverError) as excinfo:
        driver.next_action(backend=backend, prompt="goal")

    assert "service_unavailable" in str(excinfo.value) or "503" in str(excinfo.value)
    assert len(calls) == PROVER_RETRY_ATTEMPTS
    # Backoff fires before the final attempt only.
    assert sleeps == [0.5, 1.0]


def test_mistral_prover_driver_does_not_retry_on_auth_failure(monkeypatch) -> None:
    import urllib.request

    from src.prover.prover import MistralProverDriver, ProverBackend, ProverDriverError

    driver = MistralProverDriver(
        api_key="test-key", base_url="https://api.mistral.ai/v1", timeout=1.0
    )
    backend = ProverBackend(
        name="leanstral", provider="mistral", model="labs-leanstral", notes="test"
    )

    sleeps: list[float] = []
    monkeypatch.setattr("src.prover.prover.time.sleep", lambda seconds: sleeps.append(seconds))

    calls: list[int] = []

    def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise _http_error(401, b'{"error":"unauthorized"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProverDriverError):
        driver.next_action(backend=backend, prompt="goal")

    # Auth failures must surface immediately — no retry, no backoff sleeps.
    assert len(calls) == 1
    assert sleeps == []
