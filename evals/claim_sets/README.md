# Claim Sets

This directory contains the canonical claim sets that drive LeanEcon v3 evaluation. Each claim is a JSON object with `id`, `tier`, `raw_claim` (natural-language input), `theorem_stub` (Lean 4 sorry stub), and `provenance` (where the claim came from). The eval harness reads these files, runs each claim through the Planner -> Formalizer -> Prover pipeline, and reports per-claim and aggregate results.

## Canonical claim sets (drive the release denominator)

These four sets are part of the canonical evaluation surface. They are referenced in `benchmark_baselines/RUNNING_BENCHMARKS.md`, the engineering log, and benchmark summaries.

| File | Claims | Scope | Purpose |
|---|---|---|---|
| `tier0_smoke.jsonl` | 3 | `release_reliable` | Quick smoke (sub-minute total) — used in CI fast lane. |
| `tier1_core_preamble_definable.jsonl` | 24 | `release_reliable` | **The release denominator.** Frozen since Session 26. PhD-qualifying preamble-backed claims across consumer theory, DP, optimization, macro primitives. |
| `tier2_frontier_preamble_definable.jsonl` | 10 | `frontier_collect` | Frontier-tier preamble claims; diagnostic only, not in the release denominator. |
| `tier2_frontier_mathlib_native.jsonl` | 3 | `frontier_collect` | Mathlib-native frontier claims; Tier 2 beta/diagnostic only. |

## Regression claim set (prover guardrail)

| File | Claims | Scope | Purpose |
|---|---|---|---|
| `regressions/prover_easy_definable.jsonl` | 5 | `release_reliable` | Direct-hypothesis prover regression. Confirms the prover still does trivial_shortcut / direct_close on the easiest 5 claims. Listed in `evals/common.py::REGRESSION_CLAIM_SETS`. Run by `local_gate` whenever the prover code path changes. |

**Note on provenance:** the regression set was originally at `evals/claim_sets/prover_easy_definable.jsonl` and was relocated to `evals/claim_sets/regressions/prover_easy_definable.jsonl` in Sprint 18. The 5 `provenance.source_path` strings were updated in Sprint 35 to point to the new location. The regression set is **not** part of the tier1/tier2 release denominator; it is a unit-test-style guardrail for the prover.

## HIL evaluation material

| File | Claims | Scope | Purpose |
|---|---|---|---|
| `phd_qual_alpha.jsonl` | 10 | not classified | **Human-in-the-loop economist evaluation material only.** Used by the Sprint 33 HIL protocol (see `docs/FRONTIER_FLYWHEEL.md`). Not part of release or frontier diagnostics. Designed to be a small, opinionated slice of the 24-claim release denominator plus a few frontier-shaped claims, so an economist reviewer can complete a session in 30-45 minutes. |

## Archive

The `archive/` subdirectory holds historical claim sets that are no longer part of the canonical benchmark surface (e.g., older `tier1_core.json`, `tier2_frontier.json` mixed-tier sets from Sprint 13-18). They are preserved for audit history and **must not be used to derive release reliability claims**. The current integrity contract is: Tier 1 preamble-definable is release reliability; Tier 2 and mathlib-native sets are public beta/diagnostic unless explicitly promoted by a future benchmark decision.

## How a claim file should be structured

Minimal example:
```json
{
  "id": "t1_<short_name>",
  "tier": "tier1_core_preamble_definable",
  "raw_claim": "A natural-language economic claim (MWG, SLP, Maschler, or first-year PhD-qualifying level).",
  "theorem_stub": "import Mathlib\nimport LeanEcon.Preamble.<...>\n\ntheorem <lean_name>\n    {<type variables>} : <statement> := by\n  sorry\n",
  "expected_category": "DEFINABLE" | "MATHLIB_NATIVE",
  "preamble_names": ["<preamble_entry_name>"],
  "provenance": {
    "source_path": "<this or a related file path>",
    "source_kind": "preamble" | "fixture" | "regression" | "sprint<N>",
    "note": "Optional human note about origin."
  }
}
```

`expected_category` is a hint to the harness about which prover path to use; `preamble_names` is used by the formalizer for preamble retrieval. Both are advisory — the harness is the authority.

## Adding a new claim

1. Decide which tier it belongs in. Default to `tier1_core_preamble_definable` if it closes against the preamble with 0 tool calls.
2. Write the `theorem_stub` with a faithful sorry and the right `import Mathlib` + `import LeanEcon.Preamble.<...>` declarations.
3. Verify the stub compiles in Lean 4 with `lake env lean LeanEcon.lean` (or by adding to the test file under `lean_workspace/`).
4. Add the claim to the appropriate JSONL file.
5. If the claim is the first of a new economic domain, add a corresponding preamble module in `lean_workspace/LeanEcon/Preamble/`.
6. Update `docs/LeanEcon Engineering Log.md` with the addition and the rationale.
7. Do **not** promote a frontier claim into the release denominator without deterministic evidence under the `release` budget profile.
