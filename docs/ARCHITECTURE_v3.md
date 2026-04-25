# Lean Econ v3 Architecture
**Version:** 3.0.0-alpha  
**Date:** 24 April 2026  
**Status:** Authoritative — Single Source of Truth for All Implementation

> Integrity note (April 22, 2026): repository code, tests, and checked-in manifests override any overstated readiness or benchmark claims elsewhere in the docs.
> Sprint 20 update (April 24, 2026): mathlib-native proving is now a first-class route. `mathlib_native_mode` gates preamble shortcuts, exposes `lean-lsp-mcp` tools to Leanstral, and records LSP/native-search usage in benchmark traces.

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

---

## 4B. Harness RAG & Model-Agnostic Mathlib Interaction
Sprint 21 moves Mathlib retrieval into the harness so the prover stays model-agnostic and every search step is auditable. The LLM no longer decides what to retrieve — it receives a state-conditioned premise list and is asked to propose the next tactic. This trades a small amount of model-specific cleverness for determinism, debuggability, and the ability to swap in any compatible LLM.

**Primitive.** `retrieve_premises(goal_state: str, k: int = 8) -> list[Premise]` lives in [src/retrieval/mathlib_rag.py](../src/retrieval/mathlib_rag.py). `MathlibRAG` loads a JSONL seed (default: [data/mathlib_rag_seed.jsonl](../data/mathlib_rag_seed.jsonl), 62 premises covering continuity, compactness, monotonicity, fixed-point, and bridge-module declarations) and persists a normalized cache at `lean_workspace/.cache/mathlib_rag.jsonl`. Scoring is a hybrid: name-token Jaccard (weight 0.75), tag bonus (capped at 0.15), and a deterministic hashing-based cosine over the goal text (weight 0.10). The output `Premise` carries `name`, `score`, `statement`, `docstring`, `file_path`, `tags`, and `dependencies`.

**Where it plugs in.** Only the `mathlib_native` claim-type path calls retrieval. The `_try_mathlib_native_harness_loop` in [src/prover/prover.py](../src/prover/prover.py) calls `_retrieve_mathlib_premises(...)` once per turn against the active goal text, builds a prompt that includes the top-k premises plus diagnostics and active goals, asks the provider for a single `apply_tactic` action, and lets the REPL apply it. After every turn the harness computes a `ProgressDelta` and stops cleanly if neither the goal set nor the structural complexity changed (the new stall test replaces the old shallow-loop heuristic).

**Observability.** Each turn emits, in order: `RetrievalEvent` (top-k, scores, latency, hit/miss against the seed), `ToolUsageTrace` (tool name, args, state hash before/after, success), `StateTransition` (goal counts, hash digests), and `ProgressDelta` (`goals_reduced`, `complexity_reduced`, `stall_detected`). All four are emitted into the benchmark JSONL and summarized at run-end via `retrieval_hit_rate@5` and `avg_tool_calls_mathlib`.

**Fault tolerance.** `MistralProverDriver.next_action` now retries on `429`, `502`, `503`, `504`, and timeouts with the same `(0.5s, 1.0s)` backoff schedule the planner uses. Auth failures and other 4xx responses surface immediately. This closes the 503/429 trace loss observed during the Sprint 21 dry runs and brings prover behaviour in line with the planner's existing tolerance.

**Honest limits (Sprint 21 baseline).** On the focused 12-claim sample the seed-based retrieval helped on only 1 of 4 mathlib-native turns (`retrieval_hit_rate@5 = 0.25`); the verified mathlib-native claim closed via `lean_leansearch`, not the harness RAG. The retrieval *primitive* is correct and the *trace* is complete — the gap is seed coverage. Sprint 22 should treat seed expansion (or a real Mathlib index built from `lake env lean --print-paths`) as the highest-leverage next step.

---

## 5. Key Invariants (Never Violate)
1. Lean kernel is the **only** authority. `lake env lean` exit code + no warnings = success.
2. Every formalization must pass **semantic faithfulness gate** (new frame-based scorer) before Prover sees it.
3. Planner never produces vacuous or identity theorems.
4. All model-facing tool calls go through `ToolSpec` registry with budget enforcement.
5. Human review gates are **not optional** in alpha — enforced in API state machine.
6. Every benchmark run must preserve claim-type observability: claim-type policy, LSP tool calls, native search attempts, mathlib-native mode usage, and — for mathlib-native turns — `RetrievalEvent`, `ToolUsageTrace`, `StateTransition`, and `ProgressDelta` payloads must be visible in summaries/history.
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
Original: 19 April 2026. Sprint 20 operating update: 24 April 2026.
