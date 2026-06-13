# LeanEcon v3 Alpha Checkpoint

**Date:** 13 June 2026  
**Status:** local alpha foundation ready for Sprint 30 release-candidate work

This document replaces the completed Sprint 26-29 working notes. Commit history
preserves those sprint plans; the active docs should now describe the current
system, its gates, and the remaining release path.

## Current Release Surface

The alpha release denominator is:

- claim set: `tier1_core_preamble_definable`
- scope: `release_reliable`
- claim type: `preamble_definable`
- requirement: frozen preamble surface plus authoritative benchmark
  `theorem_stub`

Frontier claim sets remain useful evaluation artifacts, but they are excluded
from release reliability:

- `tier2_frontier_preamble_definable`
- `tier2_frontier_mathlib_native`

Those claims should produce scope-separated metrics and frontier queue records
instead of diluting the release denominator.

## Evidence From The Current Checkpoint

Deterministic gates recently passed:

```bash
./.venv/bin/python -m pytest -o addopts=''
lake env lean LeanEcon.lean
./.venv/bin/python -m evals.local_gate --claim-set tier0_smoke --limit 0 --output-dir /private/tmp/leanecon-s30-scaffold --allow-unready
```

Observed results:

- Python suite: `285 passed`
- Fast Lean root check: passed
- Scaffold local-gate: passed and wrote only to the selected temp directory
- Tier 1 release run: `24/24` release-reliable claims verified, average total
  latency about `34.5s`, no frontier records, no LSP/native tool calls
- Tier 2 mathlib single-claim probe after timeout hardening: completed `1/1`
  in about `198.9s` instead of hanging

The full `lake build LeanEcon` path remains expensive on local machines because
Lake may replay a large Mathlib trace graph. Treat it as an infrastructure or
release image gate, not the normal edit-loop check.

## Active System Guarantees

- Planner, formalizer, prover, API responses, benchmark summaries, and local
  gates carry `claim_scope`, `claim_type`, failure class, recommended next
  action, and frontier queue metadata.
- Prover state configuration is now execution policy, not just prompt text:
  state allowlists, per-state call caps, and decomposition permissions are
  enforced.
- API proof jobs are accepted into a bounded queue; only the configured number
  can enter `running_prover` concurrently.
- LSP readiness can be reported as ready, disabled, or unavailable without
  implicitly spawning long-lived processes.
- Frontier proof attempts are bounded by target deadlines and provider timeout
  clamping, so expensive searches terminate cleanly.

## Known Limits

- The release surface is intentionally narrow. It covers the frozen preamble
  denominator, not all undergraduate economics.
- Frontier mathlib-native work is still research-grade. It should be collected
  and inspected, not promised as reliable alpha behavior.
- Hosted deployment is not active. Railway should wait until Sprint 30 local
  release-candidate gates are green.
- Full Lake builds need a cache/image strategy before they can be relied on as
  a fast developer gate.

## Recommended Local Evaluation Commands

Use these before any deploy decision:

```bash
./.venv/bin/python -m pytest -o addopts=''
lake env lean LeanEcon.lean
./.venv/bin/python -m evals.local_gate --claim-set tier1_core_preamble_definable --output-dir /private/tmp/leanecon-alpha-tier1 --allow-unready
./.venv/bin/python -m evals.local_gate --claim-set tier2_frontier_preamble_definable --output-dir /private/tmp/leanecon-alpha-tier2-preamble --allow-unready
./.venv/bin/python -m evals.local_gate --claim-set tier2_frontier_mathlib_native --output-dir /private/tmp/leanecon-alpha-tier2-mathlib --allow-unready
```

The first three are release confidence gates. The tier 2 commands are frontier
diagnostics unless the release scope is deliberately expanded.
