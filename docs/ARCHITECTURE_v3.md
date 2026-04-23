# Lean Econ v3 Architecture
**Version:** 3.0.0-alpha  
**Date:** 19 April 2026  
**Status:** Authoritative — Single Source of Truth for All Implementation

> Integrity note (April 22, 2026): repository code, tests, and checked-in manifests override any overstated readiness or benchmark claims elsewhere in the docs.

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
│  PROVER (APOLLO + Goedel)    │  ← Goedel-Prover-V2 + self-correction
│  • Recursive sub-lemma decomp│
│  • Lean REPL fast path       │
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
- **src/prover/** — Goedel-Prover-V2 primary + Leanstral fallback. APOLLO recursive decomposition. Self-correction loop using Lean compiler feedback. ToolSpec registry for lean-lsp-mcp actions.
- **src/guardrails/** — Vacuity rejection, semantic faithfulness (new frame-based), compile check, repair history.
- **src/memory/** — SQLite + vector index (episodic proof traces, successful/failed tactics, retrieval for Planner/Prover).
- **src/observability/** — SSE streaming, typed progress events, cost tracking, tool budgets, provenance, `/health` + `/metrics`.
- **src/tools/** — Standardized `ToolSpec` (name, args, description, Lean-specific, cost). Registry + LeanInteract wrappers, with Lean LSP kept as experimental.
- **src/api/** — FastAPI v3 (async jobs, SSE, review gates).

### Knowledge Layers (Fat Skills)
- **lean_workspace/LeanEcon/Preamble/** — Versioned Lean modules (EconLib Mini). Metadata in `preamble_library.py`.
- **skills/** — Process knowledge (lean4_proving.md, econ_preamble_model.md, faithfulness_rubric.md, hilbert_protocol.md). Loaded at runtime.

---

## 3. Model Provider Strategy (Open-First, HF Primary)
**Production (Docker/Railway)**: Hugging Face Inference Endpoints or `huggingface_hub` client.
- **Planner**: MiniMaxAI/MiniMax-M2.7 or arcee-ai/Trinity-Large-Thinking or google/gemma-4-31B-it (via HF) — strongest open informal reasoner.
- **Formalizer**: `mistralai/Leanstral-2603` (119B MoE, 6.5B active, Apache 2.0) — native Lean 4 code agent.
- **Prover**: `Goedel-LM/Goedel-Prover-V2-32B` (SOTA open ATP, self-correction, 90.4 % MiniF2F) — primary. Leanstral fallback.

**Local Research/Audit (Feynman)**: Ollama with 32B+ class (Qwen2.5-Coder-32B or DeepSeek-R1-32B distilled). Zero cost, full context, perfect for gap analysis vs HILBERT paper.

**Fallback**: Codex CLI → Ollama (same 32B+ model) if token limits hit (rare).

**No Anthropic. No Opus. No rate-limit theater.**

---

## 4. Data & State
- **Job Store**: SQLite (async verification, SSE subscribers, review state).
- **Memory**: SQLite + sentence-transformers embeddings (local or HF) for semantic retrieval of past traces.
- **Preamble**: Lean source of truth + JSON metadata index (versioned, reproducible lake build).
- **Benchmarks**: `evals/claim_sets/` + `benchmark_baselines/v3_alpha/` (pinned model SHAs, exact prompts).

---

## 5. Key Invariants (Never Violate)
1. Lean kernel is the **only** authority. `lake env lean` exit code + no warnings = success.
2. Every formalization must pass **semantic faithfulness gate** (new frame-based scorer) before Prover sees it.
3. Planner never produces vacuous or identity theorems.
4. All tool calls go through `ToolSpec` registry with budget enforcement.
5. Human review gates are **not optional** in alpha — enforced in API state machine.
6. Every PR updates `benchmark_baselines/` and must not regress local-gate.

---

## 6. Deployment & CI
- **Docker**: Multi-stage, cached Lean base image (GHCR), HF model weights cached at build (for self-hosting path).
- **Railway**: Same as v2 but with v3 env vars (`LEANECON_PLANNER_MODEL=hf:Qwen2.5-72B`, etc.).
- **CI Gate**: `.github/workflows/ci.yml` runs full local-gate benchmark on every PR. Fail if tier1_core < 22/23 or tier2_frontier < 8/13.

---

## 7. Evolution Path (Post-Alpha)
- v3.1: Memory retrieval in Planner (few-shot from successful traces).
- v3.2: Full EconLib Mini open-sourced (120+ entries, searchable).
- v3.3: Pedagogical tutor frontend (Lovable or custom) with Socratic mode.
- v4.0: Multi-agent company (CEO + FormalizerResearcher + ProverResearcher) via Paperclip or custom orchestration (deferred until 500+ labeled traces).

This architecture is deliberately **simple enough to reason about** and **powerful enough to hit PhD-qualifying coverage**.

— User, Founder and Grok, CTO  
19 April 2026
