# Lean Econ v3 Architecture
**Version:** 3.0.0-alpha  
**Date:** 27 April 2026  
**Status:** Authoritative — Single Source of Truth for All Implementation

> Integrity note (April 22, 2026): repository code, tests, and checked-in manifests override any overstated readiness or benchmark claims elsewhere in the docs.
> Sprint 20 update (April 24, 2026): mathlib-native proving is now a first-class route. `mathlib_native_mode` gates preamble shortcuts, exposes `lean-lsp-mcp` tools to Leanstral, and records LSP/native-search usage in benchmark traces.
> Sprint 24 update (April 27, 2026): The cumulative hybrid retrieval pipeline is described across §4A (Sprint 20 LSP surface + Sprint 22 LeanSearch merge) and §4B (Sprints 21–24: harness RAG, semantic embedding, seed expansion, enrichment, stall recovery, observable failures, rescue retrieval). Headline `tier2_frontier_mathlib_native` pass rate has held at 1/3 across Sprints 20–24 — the synthesis bottleneck is now isolated and the Sprint 25 work plan attacks it from the prover side.

## 1. High-Level Flow (Matches Your Hand-Drawn Sketch)
```
Natural-language economic claim
        │
        ▼
┌──────────────────────────────┐
│  PLANNER (HILBERT-style)     │  ← Strongest open model (Qwen/DeepSeek 70B+ via HF)
│  • Clarifying questions      │
│  • Textbook defaults (SLP)   │
│  • Plan sketch + subgoals    │
│  • Human review gate         │
└──────────────┬───────────────┘
               │ (approved plan)
               ▼
┌──────────────────────────────┐
│  FORMALIZER                  │  ← Leanstral-2603 or Goedel-Prover-V2-32B
│  • Structured Preamble ctx   │
│  • Semantic faithfulness     │
│  • Vacuity guard             │
│  • Lean 4 sorry stub         │
│  • Human review gate         │
└──────────────┬───────────────┘
               │ (approved stub)
               ▼
┌──────────────────────────────┐
│  PROVER (APOLLO + Leanstral) │  ← Leanstral/Goedel + self-correction
│  • Recursive sub-lemma decomp│
│  • Lean REPL fast path       │
│  • mathlib_native_mode       │
│  • lean-lsp-mcp search/tools │
│  • Memory trace retrieval    │
│  • Tool-use guardrails       │
│  • Lean kernel verification  │
└──────────────┬───────────────┘
               │ (verified / failed + trace)
               ▼
┌──────────────────────────────┐
│  EXPLAINER + MEMORY          │  ← Deterministic + episodic traces
│  • Human-readable proof      │
│  • Failure diagnosis         │
│  • Store in SQLite + vector  │
└──────────────────────────────┘
```

Human review is now only claimed where the API actually supports it: review-gate jobs can be approved or rejected through `POST /jobs/{job_id}/review`. Benchmark runs continue to bypass review gates.

---

## 2. Layered Module Design (Thin Harness)
All intelligence lives in **skills/** + **Preamble** + model prompts. Everything should be model agnostic so we don't depend on a few providers to function.

Python harness is deliberately minimal.

### Core Modules
- **src/planner/** — HILBERT informal reasoner (clarifying questions, textbook defaults, plan sketch). Uses strongest HF model.
- **src/formalizer/** — Driver protocol (Leanstral / Goedel-Prover-V2 / future). Structured context builders (role-labeled: defs, lemmas, templates, tactic_hints). New semantic-frame faithfulness scorer.
- **src/prover/** — Claim-type-aware prover. Preamble-definable claims use bounded direct closure against LeanEcon metadata. Mathlib-native claims enter `mathlib_native_mode`, cap preamble-style direct closure, and invoke bounded `lean-lsp-mcp` inspection/search before provider turns. APOLLO recursive decomposition remains available when the target has a real structural boundary.
- **src/guardrails/** — Vacuity rejection, semantic faithfulness (new frame-based), compile check, repair history.
- **src/memory/** — SQLite + vector index (episodic proof traces, successful/failed tactics, retrieval for Planner/Prover).
- **src/observability/** — SSE streaming, typed progress events, cost tracking, tool budgets, provenance, `/health` + `/metrics`. Tool budgets now report total tool calls, LSP tool calls, native search attempts, and `mathlib_native_mode` uses.
- **src/tools/** — Standardized `ToolSpec` (name, args, description, Lean-specific, cost). Registry + LeanInteract wrappers + `lean-lsp-mcp` tools (`lean_goal`, `lean_code_actions`, `lean_diagnostic_messages`, `lean_hover_info`, `lean_leansearch`, `lean_loogle`).
- **src/api/** — FastAPI v3 (async jobs, SSE, review gates).

### Knowledge Layers (Fat Skills)
- **lean_workspace/LeanEcon/Preamble/** — Versioned Lean modules (EconLib Mini). Metadata in `preamble_library.py`.
- **skills/** — Process knowledge (lean4_proving.md, econ_preamble_model.md, faithfulness_rubric.md, hilbert_protocol.md). Loaded at runtime.

---

## 3. Model Provider Strategy (Open-First, Mistral/Leanstral Current Default)
**Current hosted benchmark default**:
- **Planner**: `mistral-structured` with `mistral-large-2512`.
- **Formalizer**: `leanstral` via Mistral.
- **Prover**: `leanstral` via Mistral, with bounded Lean REPL and `lean-lsp-mcp` tooling.

**Supported/open path**:
- **Planner**: MiniMaxAI/MiniMax-M2.7, arcee-ai/Trinity-Large-Thinking, Qwen/DeepSeek class models, or other strong open informal reasoners via HF.
- **Formalizer**: `mistralai/Leanstral-2603` — native Lean 4 code agent.
- **Prover**: `Goedel-LM/Goedel-Prover-V2-32B` remains supported as an open ATP backend; Leanstral is the current mathlib-native proving focus because it is optimized for Lean and `lean-lsp-mcp` workflows.

**Local Research/Audit (Feynman)**: Ollama with 32B+ class (Qwen2.5-Coder-32B or DeepSeek-R1-32B distilled). Zero cost, full context, perfect for gap analysis vs HILBERT paper.

**Fallback**: Codex CLI → Ollama (same 32B+ model) if token limits hit (rare).

**No Anthropic. No Opus. No rate-limit theater.**

---

## 4. Data & State
- **Job Store**: SQLite (async verification, SSE subscribers, review state).
- **Memory**: SQLite + sentence-transformers embeddings (local or HF) for semantic retrieval of past traces.
- **Preamble**: Lean source of truth + JSON metadata index (versioned, reproducible lake build).
- **Benchmarks**: `evals/claim_sets/` + `benchmark_baselines/v3_alpha/` (pinned model SHAs, exact prompts). Canonical benchmark buckets separate difficulty from type: `tier1_core_preamble_definable`, `tier2_frontier_preamble_definable`, and `tier2_frontier_mathlib_native`.

---

## 4A. Claim-Type Routing and Lean LSP

> §4A and §4B together describe the cumulative hybrid retrieval pipeline. §4A covers the LSP surface (Sprint 20) and `lean_leansearch` merging (Sprint 22). §4B covers the harness-owned RAG primitive (Sprint 21), semantic embedding (Sprint 22), seed expansion + enrichment + stall recovery (Sprint 23), and observable failure paths + rescue retrieval (Sprint 24).

The prover receives `claim_type` from the benchmark manifest/formalization packet when available:

- `preamble_definable`: LeanEcon Preamble metadata and proven lemmas are trusted as the first search surface. Direct closure remains enabled up to the normal bounded cap.
- `mathlib_native`: the prover sets `mathlib_native_mode=True`, disables Preamble-derived shortcut use, allows only a tiny compile-checked direct-close budget, and then uses `lean-lsp-mcp` to inspect the proof state and search Mathlib.

The mathlib-native route currently performs a bounded LSP pass:

1. `lean_diagnostic_messages` around the active proof line.
2. `lean_goal` at the active proof position.
3. `lean_code_actions` for "try this" tactics.
4. `lean_hover_info` for local type context.
5. `lean_leansearch` over the natural-language claim, theorem goal, and active goal.
6. Compile-check candidate tactics extracted from code actions, search results, and narrow mathlib heuristics.

Every trace step is enriched with `claim_type`, `claim_type_policy`, `target_kind`, `mathlib_native_mode`, `lsp_tool_call`, and `native_search_attempt` so subgoals and theorem bodies are equally auditable.

**Sprint 22 addition.** `_try_mathlib_native_harness_loop()` now calls `lean_leansearch` as a first-class harness retrieval primitive (not just a shortcut candidate generator). Results are merged with local `MathlibRAG` results via `_merge_retrieval_premises()` (dedup by name, sort by score descending) before the LLM prompt is built. Every LeanSearch call emits a `RetrievalEvent(source="lean_leansearch", query=...)` with latency and hit flag alongside the local RAG event, so both sources are visible in benchmark JSONL traces.

---

## 4B. Harness RAG & Model-Agnostic Mathlib Interaction
Sprint 21 moves Mathlib retrieval into the harness so the prover stays model-agnostic and every search step is auditable. The LLM no longer decides what to retrieve — it receives a state-conditioned premise list and is asked to propose the next tactic. This trades a small amount of model-specific cleverness for determinism, debuggability, and the ability to swap in any compatible LLM.

**Primitive.** `retrieve_premises(goal_state: str, k: int = 8) -> list[Premise]` lives in [src/retrieval/mathlib_rag.py](../src/retrieval/mathlib_rag.py). `MathlibRAG` loads a JSONL seed (default: [data/mathlib_rag_seed.jsonl](../data/mathlib_rag_seed.jsonl), 62 premises covering continuity, compactness, monotonicity, fixed-point, and bridge-module declarations) and persists a normalized cache at `lean_workspace/.cache/mathlib_rag.jsonl`. Scoring is a hybrid: name-token Jaccard (weight 0.75), tag bonus (capped at 0.15), and a deterministic hashing-based cosine over the goal text (weight 0.10). The output `Premise` carries `name`, `score`, `statement`, `docstring`, `file_path`, `tags`, and `dependencies`.

**Where it plugs in.** Only the `mathlib_native` claim-type path calls retrieval. The `_try_mathlib_native_harness_loop` in [src/prover/prover.py](../src/prover/prover.py) calls `_retrieve_mathlib_premises(...)` once per turn against the active goal text, builds a prompt that includes the top-k premises plus diagnostics and active goals, asks the provider for a single `apply_tactic` action, and lets the REPL apply it. After every turn the harness computes a `ProgressDelta` and stops cleanly if neither the goal set nor the structural complexity changed (the new stall test replaces the old shallow-loop heuristic).

**Observability.** Each turn emits, in order: `RetrievalEvent` (top-k, scores, latency, hit/miss against the seed), `ToolUsageTrace` (tool name, args, state hash before/after, success), `StateTransition` (goal counts, hash digests), and `ProgressDelta` (`goals_reduced`, `complexity_reduced`, `stall_detected`). All four are emitted into the benchmark JSONL and summarized at run-end via `retrieval_hit_rate@5` and `avg_tool_calls_mathlib`.

**Fault tolerance.** `MistralProverDriver.next_action` now retries on `429`, `502`, `503`, `504`, and timeouts with the same `(0.5s, 1.0s)` backoff schedule the planner uses. Auth failures and other 4xx responses surface immediately. This closes the 503/429 trace loss observed during the Sprint 21 dry runs and brings prover behaviour in line with the planner's existing tolerance.

**Honest limits (Sprint 21 baseline).** On the focused 12-claim sample the seed-based retrieval helped on only 1 of 4 mathlib-native turns (`retrieval_hit_rate@5 = 0.25`); the verified mathlib-native claim closed via `lean_leansearch`, not the harness RAG. The retrieval *primitive* is correct and the *trace* is complete — the gap is seed coverage.

**Sprint 22 addition.** `_default_rag()` now delegates embedder selection to `get_default_embedder()` (tries `SentenceTransformerEmbedder` — `sentence-transformers/all-MiniLM-L6-v2` by default — and falls back to `HashingTextEmbedder` on any failure). Set `LEANECON_LOCAL_FILES_ONLY=true` to prevent model downloads in CI; set `LEANECON_EMBEDDING_MODEL` to override the model name. The Sprint 21 honest baseline (0.25 hit rate) reflects the lexical-only seed; Sprint 22 target is ≥ 0.60 after merging LeanSearch results and semantic scoring.

**Sprint 23 addition (synthesis lift).** With Sprint 22 retrieval at 100% hit rate but only 1/3 pass@1 on the focused mathlib-native sample, the bottleneck shifted to *using* the retrieved premises. Sprint 23 lands four pieces: (1) the local seed grew from 62 → ~1500 entries via `scripts/extract_mathlib_premises.py` (regex extractor over curated Topology / Order / Analysis / FixedPoints subdirectories of Mathlib), so the failing extreme-value and monotone-convergence claims now have rich premise coverage; (2) LeanSearch results are enriched with `lean_file_outline` + `lean_hover_info` (cached per file) so each premise carries `full_type_signature` and `detailed_docstring` instead of the thin leansearch payload; (3) a stall-recovery second leansearch pass fires when turn 1 makes no progress and ≥30% of search budget remains, requerying with the current unsolved subgoal text; (4) a generic decomposition hint (`intro/obtain/refine` for goals with `∀/∃/∧/↔`) is appended to the prompt rules. Mathlib-native claims also receive a hybrid budget bump (`MAX_SEARCH_TOOL_CALLS_HYBRID = +2`, `MAX_PROVE_STEPS_HYBRID = +4`) — preamble_definable budgets are unchanged. New traces: `RetrievalEvent.enriched_count`, `RetrievalEvent.retrieval_pass`; new aggregator metrics: `second_retrieval_rate`, `enriched_leansearch_hit_rate`.

**Sprint 24 addition (observable failures + rescue retrieval).** Sprint 23's synthesis-lift infrastructure was complete but several harness-side failures were still silent. Sprint 24 closes the observability gaps and adds two narrowly-scoped recovery primitives. (1) `LeanSearchFailureEvent` (in `src/observability/models.py`) gives 0-result and exception failures structured visibility — `_retrieve_lean_search_premises` now emits the event, retries once with a refined sub-goal query, and preserves all budget/recording semantics on the success path. (2) Rescue retrieval keyed on `unknown identifier` errors: `_extract_unknown_identifier` lifts identifiers from Lean error text, `_query_from_failed_identifier` splits snake/camel case into a plain-English query (e.g., `MonotoneBddAboveConverges` → `"monotone bdd above converges theorem"`), and the harness fires one extra `lean_leansearch` call before stalling. Tracked via `_rescue_retrieval_targets` per-target idempotence sets so a single hallucinated identifier triggers at most one rescue. (3) Decomposition hints from Sprint 23 are strengthened with explicit multi-step pattern examples. (4) Empty-goal harness skip avoids false-positive stall when an LSP probe shows no goal state. (5) Infrastructure consolidation: shared JSON extraction in `src/utils/json_extraction.py` (per-module wrappers retained for module-specific error context), and a dedicated `src/prover/lsp_cache.py` (`LSPCache` with SHA256-keyed invalidation). Result: full local_gate at **11/16 (68.8%)**, `tier2_frontier_mathlib_native` still at **1/3**. Headline rate did not move; the remaining failures are now visible at every step. The cumulative result of Sprints 20–24 isolates synthesis as the wall — Sprint 25 must change the model-side approach (different prover model, different prompting strategy, or different decomposition primitives) rather than adding more retrieval surface.

**Sprint 25 addition (ProofSynthesizer + synthesis metrics).** Sprint 25 adds a model-agnostic `ProofSynthesizer` boundary in [src/prover/synthesizer.py](../src/prover/synthesizer.py). The harness now builds a deterministic `ProofSketch` before provider turns, using planner paragraph/subgoals plus premise conclusion overlap to surface `strategy`, `likely_premises`, `subgoal_order`, and `tactic_shape`. Both the fallback prover prompt and the mathlib-native harness prompt include three capped, generic few-shot tactic patterns: fixed-point direct premise use, compact/extreme-value witness extraction, and monotone bounded convergence via a `Tendsto` theorem. Decomposition hints now also trigger on compactness/maximum and order/convergence markers (`IsCompact`, `ContinuousOn`, `IsMaxOn`, `Monotone`, `BddAbove`, `Tendsto`, `atTop`, `sSup`, `ciSup`).

The harness emits `SynthesisEvent` after each mathlib-native `apply_tactic`, recording tactic text, retrieved premise names referenced by the tactic, top-3 premise match status, success, target, claim, and decomposition depth. Benchmark summaries now include `synthesis_efficiency`, `premise_match_rate@3`, and `avg_decomposition_depth_mathlib`; history rows persist the same metrics. On `ProgressDelta.stall_detected`, the harness can request one small helper lemma through the existing decomposition path when recursion budget remains, then records verified helpers as `memory_kind="mathlib_helper_lemma"` via `ProverMemoryWriter`. Future prompts can retrieve those helpers with `ProofTraceStore.query_mathlib_helpers(...)`, keeping cache pollution isolated from ordinary preamble examples. Best-of-N provider sampling is present but deterministic by default (`MATHLIB_SYNTHESIS_BEST_OF_N=1`).

---

## 5. Key Invariants (Never Violate)
1. Lean kernel is the **only** authority. `lake env lean` exit code + no warnings = success.
2. Every formalization must pass **semantic faithfulness gate** (new frame-based scorer) before Prover sees it.
3. Planner never produces vacuous or identity theorems.
4. All model-facing tool calls go through `ToolSpec` registry with budget enforcement.
5. Human review gates are **not optional** in alpha — enforced in API state machine.
6. Every benchmark run must preserve claim-type observability: claim-type policy, LSP tool calls, native search attempts, mathlib-native mode usage, and — for mathlib-native turns — `RetrievalEvent`, `ToolUsageTrace`, `StateTransition`, `ProgressDelta`, and `SynthesisEvent` payloads must be visible in summaries/history.
7. Every PR updates `benchmark_baselines/` and must not regress local-gate.

---

## 6. Deployment & CI
- **Docker**: Multi-stage, cached Lean base image (GHCR), HF model weights cached at build (for self-hosting path).
- **Railway**: Same core pattern as v2 with v3 env vars, Mistral/Leanstral credentials, cached Lean workspace, and `uvx lean-lsp-mcp` available in the runtime image. Deployment readiness requires `/health`, `/metrics`, `lake build`, and benchmark-mode smoke/local-gate checks.
- **CI Gate**: `.github/workflows/ci.yml` should target the normalized benchmark surface, not the historical mixed files: `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`.

---

## 7. Evolution Path (Post-Alpha)
- v3.1: Memory retrieval in Planner (few-shot from successful traces).
- v3.2: Full EconLib Mini open-sourced (120+ entries, searchable).
- v3.3: Pedagogical tutor frontend (Lovable or custom) with Socratic mode.
- v4.0: Multi-agent company (CEO + FormalizerResearcher + ProverResearcher) via Paperclip or custom orchestration (deferred until 500+ labeled traces).

This architecture is deliberately **simple enough to reason about** and **powerful enough to hit PhD-qualifying coverage**.

— User, Founder and Grok, CTO  
Original: 19 April 2026. Sprint 20 operating update: 24 April 2026. Sprint 24 operating update: 27 April 2026.
