"""HILBERT-style planner skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.skills import load_skill


@dataclass(frozen=True)
class PlannerPacket:
    claim: str
    clarifying_questions: list[str]
    textbook_defaults: list[str]
    plan_sketch: str
    subgoals: list[str]
    review_state: str
    benchmark_mode: bool = False


@dataclass
class PlannerService:
    prompt_spec_name: str = "hilbert_protocol"
    prompt_spec: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.prompt_spec = load_skill(self.prompt_spec_name)

    def build_plan(self, claim: str, *, benchmark_mode: bool = False) -> PlannerPacket:
        lowered = claim.lower()
        questions: list[str] = []
        defaults: list[str] = []
        if "bellman" in lowered or "dynamic" in lowered:
            defaults.append("Assume Stokey-Lucas-Prescott bounded return and beta in (0,1).")
        if "nash" in lowered or "game" in lowered:
            defaults.append("Use finite-action Maschler-style normal-form defaults.")
        if not defaults:
            defaults.append("Use textbook defaults from MWG unless the claim overrides them.")
        if "if" not in lowered and "for all" not in lowered:
            questions.append("Should universal domain assumptions be made explicit before formalization?")
        plan_sketch = (
            "Interpret the economic claim under textbook defaults, isolate the target objects, "
            "and hand the approved structure to the formalizer as Lean sorry subgoals."
        )
        subgoals = [
            "theorem plan_subgoal_1 : True := by\n  sorry",
            "theorem plan_subgoal_2 : True := by\n  sorry",
        ]
        review_state = "approved" if benchmark_mode else "awaiting_plan_review"
        return PlannerPacket(
            claim=claim,
            clarifying_questions=questions,
            textbook_defaults=defaults,
            plan_sketch=plan_sketch,
            subgoals=subgoals,
            review_state=review_state,
            benchmark_mode=benchmark_mode,
        )
