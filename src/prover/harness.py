"""Minimal APOLLO-oriented verification harness."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from src.config import MAX_PROVE_STEPS, MAX_PROVE_TIMEOUT, REPL_ENABLED
from src.lean import LeanREPLSession, classify_lean_errors, compile_check
from src.observability import BudgetTracker, SpanRecorder, attach_telemetry, dominant_failure_class, preamble_names_from_theorem
from src.prover.file_controller import ProofFileController


def direct_hypothesis_name(theorem_with_sorry: str) -> str | None:
    goal_match = re.search(r":\s*\n\s*(.+?)\s*:=\s*by", theorem_with_sorry, re.DOTALL)
    if goal_match is None:
        return None
    goal = " ".join(goal_match.group(1).split())
    for match in re.finditer(r"\((?P<name>[A-Za-z0-9_']+)\s*:\s*(?P<body>[^)]*)\)", theorem_with_sorry):
        body = " ".join(match.group("body").split())
        if body == goal:
            return match.group("name")
    return None


def suggest_fast_path_tactics(theorem_with_sorry: str) -> list[str]:
    direct = direct_hypothesis_name(theorem_with_sorry)
    if direct:
        return [f"exact {direct}", f"simpa using {direct}"]
    if "nkpc" in theorem_with_sorry:
        return ["simpa [nkpc]", "ring"]
    return ["simp", "aesop", "norm_num"]


@dataclass
class VerificationHarness:
    file_controller: ProofFileController
    budget_tracker: BudgetTracker

    async def verify(
        self,
        theorem_with_sorry: str,
        job_id: str,
        *,
        max_steps: int = MAX_PROVE_STEPS,
        timeout: int = MAX_PROVE_TIMEOUT,
        tactic_hint: str | None = None,
    ) -> SimpleNamespace:
        telemetry = SpanRecorder()
        self.file_controller.initialize(job_id, theorem_with_sorry)
        theorem_name = self._theorem_name(theorem_with_sorry)

        attempts: list[dict[str, Any]] = []
        current_code = theorem_with_sorry
        tactics = [tactic_hint] if tactic_hint else []
        tactics.extend(tactic for tactic in suggest_fast_path_tactics(theorem_with_sorry) if tactic)
        tactics = tactics[: max_steps or 1]

        if REPL_ENABLED and LeanREPLSession is not None:
            lean_started = telemetry.started_at
            try:
                with LeanREPLSession(timeout=timeout) as repl:
                    repl.start_proof(theorem_with_sorry, timeout=timeout)
                    for step, tactic in enumerate(tactics, start=1):
                        attempts.append({"step": step, "tactic": tactic})
                        result = repl.apply_tactic(tactic, timeout=timeout)
                        if hasattr(result, "has_errors") and result.has_errors():
                            continue
                        current_code = repl.materialize_proof()
                        self.file_controller.write_current_code(job_id, current_code)
                        if getattr(result, "proof_status", "") == "Completed":
                            compile_result = repl.verify_materialized_proof(
                                filename=f"{job_id}_repl_{step}.lean",
                                timeout=timeout,
                            )
                            compile_result["classified_errors"] = [
                                item.to_dict()
                                for item in classify_lean_errors(
                                    compile_result.get("output", ""),
                                    list(compile_result.get("errors") or []),
                                )
                            ]
                            if compile_result["success"]:
                                payload = {
                                    "status": "verified",
                                    "theorem": theorem_name,
                                    "compile": compile_result,
                                    "attempts": attempts,
                                    "tool_budget": self.budget_tracker.snapshot(),
                                    "termination_reason": "verified",
                                }
                                return SimpleNamespace(
                                    status="completed",
                                    result=attach_telemetry(payload, telemetry, {"repl_used": True}),
                                )
            finally:
                telemetry.record_lean(lean_started)

        compile_result = compile_check(current_code, timeout=timeout, filename=f"{job_id}_final.lean")
        compile_result["classified_errors"] = [
            item.to_dict()
            for item in classify_lean_errors(
                compile_result.get("output", ""),
                list(compile_result.get("errors") or []),
            )
        ]
        failure_class = dominant_failure_class(compile_result["classified_errors"])
        status = "verified" if compile_result["success"] else "failed"
        payload = {
            "status": status,
            "theorem": theorem_name,
            "compile": compile_result,
            "attempts": attempts,
            "tool_budget": self.budget_tracker.snapshot(),
            "termination_reason": "verified" if compile_result["success"] else "initial_compile_invalid",
            "failure_class": failure_class,
            "preamble_names": preamble_names_from_theorem(theorem_with_sorry),
        }
        return SimpleNamespace(
            status="completed" if compile_result["success"] else "failed",
            result=attach_telemetry(payload, telemetry, {"repl_used": False}),
            error=None if compile_result["success"] else "Verification failed.",
        )

    @staticmethod
    def _theorem_name(theorem_with_sorry: str) -> str:
        match = re.search(r"^\s*(?:theorem|lemma)\s+([A-Za-z0-9_']+)", theorem_with_sorry, re.MULTILINE)
        return match.group(1) if match else "anonymous_theorem"
