# Decision: Sprint 31 Provider Strategy

**Date:** 14 June 2026  
**Status:** accepted for alpha implementation

## Context

The alpha release denominator is frozen to `tier1_core_preamble_definable` with
`claim_scope = release_reliable`. Frontier and research runs are useful for
diagnostics, but they must not change release reliability or silently alter
public-provider behavior.

## Decision

LeanEcon v3 keeps Mistral-primary provider defaults for the `release` budget
profile:

- planner: `mistral-large-2512`
- formalizer: `labs-leanstral-2603`
- prover: `labs-leanstral-2603`
- prover fallback: `leanstral`/Mistral only

Non-Mistral provider paths, including cheap planner or prover fallback
exploration, are allowed only under `frontier` or `research`. Those profiles are
non-release and are excluded from `release_reliable_metrics`. `research` is
local-only.

## Consequences

- Public/API alpha paths default to `LEANECON_BUDGET_PROFILE=release`.
- `/health`, `/metrics`, API job payloads, local-gate summaries, and benchmark
  history report the active budget profile and provider guardrail status.
- Release-profile API proof jobs reject mathlib-native/frontier packets and
  reject non-Mistral fallback configuration instead of accepting it silently.
- Frontier diagnostics must opt in with `--budget-profile frontier` or
  request/API profile `frontier`.
- Future provider experiments must be documented as non-release unless a later
  decision explicitly changes the release provider policy.
