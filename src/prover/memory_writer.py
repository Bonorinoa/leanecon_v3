"""Persist prover traces for later planner/prover retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from src.formalizer.models import FormalizationPacket
from src.memory import ProofTrace, ProofTraceStore
from src.observability import utc_now
from src.prover.models import ProverResult
from src.prover.tactics import summarize_lesson


@dataclass
class ProverMemoryWriter:
    """Convert prover results into episodic-memory traces."""

    trace_store: ProofTraceStore

    def record(self, packet: FormalizationPacket, result: ProverResult) -> ProofTrace:
        tactic_sequence = [
            step.tool_arguments.get("tactic", "").strip()
            for step in result.trace
            if step.tool_name == "apply_tactic"
            and isinstance(step.tool_arguments.get("tactic"), str)
            and step.tool_arguments.get("tactic", "").strip()
        ]
        trace = ProofTrace(
            claim_id=packet.theorem_name,
            claim_text=packet.claim,
            preamble_names=list(packet.selected_preamble),
            tactic_sequence=tactic_sequence,
            stage_outcomes={"formalizer": "approved", "prover": result.status},
            failure_class=result.failure.reason if result.failure is not None else None,
            repair_count=result.repair_count,
            outcome=result.status,
            formalizer_model=packet.model,
            timestamp=utc_now(),
            lesson_summary=summarize_lesson(
                outcome=result.status,
                tactic_sequence=tactic_sequence,
                preamble_names=list(packet.selected_preamble),
                termination_reason=result.termination_reason,
            ),
            full_trace=result.model_dump(mode="json"),
            prover_backend=result.backend_used,
            trace_metadata={
                "theorem_name": result.theorem_name,
                "attempted_backends": list(result.attempted_backends),
                "termination_reason": result.termination_reason,
            },
        )
        self.trace_store.record(trace)
        return trace
