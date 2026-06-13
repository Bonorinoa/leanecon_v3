# LeanEcon v3 Sprint 26-30 Roadmap

**Status:** Planning document  
**Date:** 13 June 2026  
**Purpose:** Define the path from the current Sprint 26 development state to a Sprint 30 redeployable LeanEcon v3 alpha.

## Executive Summary

Sprints 26-30 should turn LeanEcon v3 from a research-heavy proving harness into a publishable alpha system with honest scope boundaries. The release target is not "general mathematical economics theorem proving." The release target is a working system that can reliably plan, formalize, and prove advanced undergraduate mathematical economics claims when those claims are inside the supported LeanEcon preamble surface, while clearly classifying frontier claims and preserving their traces for future work.

The central strategic move is to separate three surfaces:

1. **Reliable surface:** preamble-definable undergraduate economics claims that should solve at high pass rates.
2. **Research surface:** mathlib-native or synthesis-heavy claims that are useful but not release-critical.
3. **Out-of-scope surface:** claims that require missing economics primitives, missing assumptions, unavailable mathlib facts, or proof methods outside the current system.

By Sprint 30, the system should make that separation explicit in API responses, benchmark summaries, and trace data.

## Expected Positive Externality

Yes, the roadmap should produce a real positive externality: a claim-classification and data-flywheel layer.

If Sprint 27 stabilizes execution and Sprint 28 makes planner/formalizer/prover contracts more explicit, then every failed claim can become useful data instead of an opaque failure. The system should classify failures as:

- missing preamble primitive,
- missing preamble theorem,
- formalization mismatch,
- insufficient assumptions,
- proof-search failure,
- mathlib-native frontier,
- provider/tooling failure,
- out of mathematical scope.

That taxonomy makes the roadmap self-improving. Non-frontier claims should increasingly route through `preamble_definable` mode and solve reliably. Frontier claims should be collected with structured metadata so they can drive:

- preamble expansion,
- template improvements,
- prompt and tactic policy changes,
- retrieval seed expansion,
- fine-tuning datasets,
- provider/model selection,
- benchmark redesign.

The key is to collect frontier traces intentionally, not mix them into release pass-rate claims.

## Scope Tiers

### Tier A: Release-Reliable

Claims in this tier should solve at least 95% of the time by Sprint 30 under benchmark-mode conditions.

Expected shape:

- claim maps to an existing LeanEcon preamble entry,
- theorem statement can be generated from known templates,
- proof closes by direct lemma use, simple tactics, or bounded deterministic search,
- assumptions are explicit and standard for undergraduate mathematical economics.

Examples:

- budget equality from an existing budget hypothesis,
- feasibility and value comparisons for constrained maxima,
- monotone bounded real sequence convergence via the local bridge theorem,
- contraction fixed-point/value-function statements,
- simple best-response and equilibrium certificate unpacking,
- Walras-law-style consequences from market-clearing assumptions.

### Tier B: Supported But Not Guaranteed

Claims in this tier are fair attempts but not release guarantees.

Expected shape:

- claim uses mathlib-native theorem search,
- proof may require synthesis beyond a direct preamble lemma,
- decomposition may be needed,
- failure should produce a useful trace and classification.

Examples:

- compactness/existence claims not yet wrapped in the preamble,
- more complex convergence statements,
- nontrivial optimization theorems requiring theorem assembly,
- multi-step equilibrium existence arguments.

### Tier C: Frontier Collection

Claims in this tier should be collected, classified, and used to drive future work. They should not count against Sprint 30 release reliability.

Expected shape:

- missing economics definitions,
- missing formal primitives,
- broad textbook theorem requiring significant library development,
- proof requires sophisticated mathlib navigation or original Lean development.

Examples:

- full Arrow-Debreu existence,
- general Kakutani-style fixed-point applications,
- envelope theorem or Roy's identity without a developed calculus preamble,
- comparative statics requiring differentiability infrastructure,
- welfare theorems beyond certificate-level wrappers.

## Data Flywheel

Every claim attempt should emit enough structured data to answer:

- Was this claim in scope?
- Which preamble entries were selected?
- Which definitions or lemmas were missing?
- Did formalization preserve the claim?
- Did proof fail because the statement was wrong, the tactic search was weak, or the library was incomplete?
- Is this a candidate for preamble expansion, prompt work, model fine-tuning, or benchmark exclusion?

Minimum frontier record:

- raw claim,
- classified tier,
- claim type,
- selected preamble entries,
- generated Lean statement,
- parse/compile result,
- prover state transitions,
- retrieval events,
- tactic attempts,
- final failure class,
- recommended next action.

The output should support these future queues:

- `preamble_gap_queue`: missing definitions and lemmas.
- `formalizer_gap_queue`: faithful Lean statements not generated reliably.
- `proof_search_gap_queue`: correct statements with failed proofs.
- `mathlib_frontier_queue`: claims requiring native theorem search or library development.
- `fine_tuning_queue`: examples with clear input, target, and correction.

## Sprint Sequence

### Sprint 26: Current Baseline And Stabilization Entry

Sprint 26 is the baseline. It introduced the explicit prover state machine and surfaced that several state fields are descriptive rather than enforced.

Sprint 26 exit expectation:

- current tests pass,
- CI failure from accidental LSP subprocess access is fixed,
- state-machine rough edges are documented,
- Sprint 27 technical plan is ready.

### Sprint 27: Technical Foundation

Theme: deterministic, efficient, dependency-injected infrastructure.

Sprint 27 should enforce state configuration, reduce accidental global dependencies, split high-risk prover execution paths, and make CI/local gates deterministic.

Primary outcome:

- the system becomes cheaper and safer to develop against.

### Sprint 28: Agentic Ecosystem On Stable Rails

Theme: planner-formalizer-prover contracts and preamble-backed reliability.

Sprint 28 should strengthen claim routing, template-first formalization, failure classification, and preamble metadata consistency.

Primary outcome:

- non-frontier undergraduate claims reliably route through `preamble_definable` mode.

Implementation checkpoint:

- scope classes are explicit in code and summaries,
- planner/formalizer/prover packets carry operational contract fields,
- preamble-backed release-reliable claims can use metadata templates before model generation,
- metadata consistency is covered by focused tests,
- local-gate writes structured frontier queues to explicit output directories,
- reliable-surface metrics are reported separately from frontier/attempt metrics.

### Sprint 29: Productization And Benchmark Freeze

Theme: freeze the release benchmark and prepare the local deployment surface.

Sprint 29 should define the release claim set, write demo examples, harden docs, and create a reproducible local deployment path.

Primary outcome:

- LeanEcon v3 can be demonstrated from a clean checkout with honest pass-rate reporting.

### Sprint 30: Redeployable Alpha Checkpoint

Theme: publishable alpha.

Sprint 30 should polish, audit, run the release gates, and redeploy only if the system meets the release bar.

Primary outcome:

- a working LeanEcon v3 alpha capable of advanced undergraduate mathematical economics within explicit scope boundaries.

## Non-Negotiable Gates

Every sprint should preserve:

- `pytest` green,
- `lake build` green,
- deterministic CI without live providers,
- no unit test requiring `uvx`, network, or credentials,
- benchmark outputs written intentionally, not as a side effect of ordinary tests,
- docs that match actual commands.

Sprint 30 additionally requires:

- release benchmark frozen,
- scope classifier active,
- frontier claim data captured,
- local deployment validated,
- hosted deployment attempted only after local release gates pass.

## Collaboration Workflow

Use this planning chat for evaluation and prompt design. Use separate Codex goal-mode sessions for implementation.

Workflow:

1. Select the sprint doc.
2. Copy the sprint's Codex goal prompt into a fresh implementation session.
3. Let that session implement and verify.
4. Bring the summary, diff, and test results back to this planning chat.
5. Evaluate the changes against the sprint gate.
6. Revise the next sprint prompt.
7. After Sprint 30, perform a final audit, cleanup, commit, push, and redeploy.
