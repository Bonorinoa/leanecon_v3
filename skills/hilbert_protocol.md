# HILBERT Protocol (v3)

## Planner Responsibilities

- Read the claim and identify ambiguity.
- Propose textbook defaults from MWG, SLP, or Mas-Colell style contexts.
- Emit a concise plan sketch and 3-5 subgoals.
- Stop at the mandatory human review gate unless `benchmark_mode=true`.

## Runtime Mapping

- `src/planner/service.py` emits the planner packet.
- `/plan` persists that packet in the job store with `awaiting_plan_review`.
- `/formalize` may consume the approved packet or bypass it only for benchmark runs.

## TODOs

- Add clarifying-question ontology.
- Replace static defaults with model-backed HILBERT prompting.
- Feed similar successful plans from episodic memory back into the planner.
