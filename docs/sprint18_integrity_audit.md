# Sprint 18 Integrity Audit

## Status
- Repository code, tests, and checked-in manifests now override aspirational claims in older project docs.
- Historical benchmark artifacts under `benchmark_baselines/v3_alpha/` are preserved as audit evidence only.
- Public score publication remains blocked until a fresh separated-mode benchmark run is produced.

## Implemented Changes
- Central backend capability metadata: `src/backend_capabilities.py`
- Honest review-gate transitions: `POST /jobs/{job_id}/review`
- Shared progress-event model for API SSE and local benchmarks: `src/observability/progress.py`
- Claim-bucket classification: `evals/claim_buckets.json`
- Benchmark integrity manifests: `evals/benchmark_manifest.py`, `evals/benchmark_manifest.json`
- Canonical core split:
  - `evals/claim_sets/tier1_core_preamble_definable.jsonl`
- Focused frontier splits:
  - `evals/claim_sets/tier2_frontier_mathlib_native.jsonl`
  - `evals/claim_sets/tier2_frontier_preamble_definable.jsonl`
- `local_gate` terminal heartbeats for long-running claims plus incremental progress logs under the selected output directory
- Historical artifact registry: `benchmark_baselines/v3_alpha/artifacts_manifest.json`

## Current Assessment
- Railway readiness: not yet claimable.
- Breadth claims: overstated by the older mixed benchmark framing.
- Streaming: meaningful stage/prover progress is now available through SSE.
- Model agnosticism: partial only; supported combinations are now declared explicitly.
- Implicit-assumption extraction: still not a standalone implemented capability.
- Public benchmark refresh: blocked pending fresh runs and manifest reconciliation.
