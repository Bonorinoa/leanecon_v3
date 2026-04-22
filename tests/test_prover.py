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
from src.prover.tactics import should_decompose


def _packet(
    *,
    theorem_name: str,
    claim: str,
    lean_code: str,
    subgoals: list[dict[str, str]] | None = None,
    selected_preamble: list[str] | None = None,
) -> FormalizationPacket:
    return FormalizationPacket.model_validate(
        {
            "claim": claim,
            "lean_code": lean_code,
            "theorem_with_sorry": lean_code,
            "theorem_name": theorem_name,
            "imports": ["Mathlib"],
            "selected_imports": ["Mathlib"],
            "open_statements": [],
            "subgoals": subgoals or [],
            "selected_preamble": ["bellman_operator"] if selected_preamble is None else selected_preamble,
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

    def next_action(self, *, backend, prompt: str) -> ProverAction:
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
async def test_prover_reports_no_progress_stall_for_unchanged_repl_state(tmp_path, monkeypatch) -> None:
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
async def test_prover_benchmark_mode_skips_memory_and_cleans_artifacts(tmp_path, monkeypatch) -> None:
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
    assert prover._timeout_for_target(
        ProverTarget(name="main", statement="True", kind="theorem_body"),
        resolved,
    ) == 300
    assert prover._timeout_for_target(
        ProverTarget(name="sub", statement="True", kind="subgoal"),
        resolved,
    ) == 180
    assert prover._timeout_for_target(
        ProverTarget(name="lemma", statement="True", kind="apollo_lemma"),
        resolved,
    ) == 300
    assert prover._final_compile_timeout(resolved) == 300


def test_prover_recursion_depth_allows_three_and_rejects_four() -> None:
    action = ProverAction.model_validate(
        {
            "action_type": "decompose",
            "rationale": "Extract a helper lemma.",
            "decomposition_statement": "True",
        }
    )

    allowed = ProverTarget(name="depth_three", statement="True", kind="apollo_lemma", recursion_depth=3)
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
async def test_prover_recovers_repl_compile_disagreement_with_compile_normalization(tmp_path, monkeypatch) -> None:
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
async def test_prover_decomposition_rewrites_subgoal_after_repl_materialization(tmp_path, monkeypatch) -> None:
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
async def test_prover_shortcut_falls_back_to_exact_question_mark(
    tmp_path, monkeypatch
) -> None:
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
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "exact True.intro"}},
                    },
                    {
                        "action_type": "tool",
                        "rationale": "Repeat the same exact proof so the prover triggers a soft repair.",
                        "tool": {"name": "apply_tactic", "arguments": {"tactic": "exact True.intro"}},
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
