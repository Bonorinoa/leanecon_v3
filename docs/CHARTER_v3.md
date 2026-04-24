# Lean Econ v3 Charter
**Version:** 3.0.0-alpha  
**Date:** 24 April 2026  
**Status:** Founding Document + Sprint 20 Operating Update  
**CTO:** Grok (xAI) — Systems Engineer & PhD Economist  
**Senior Engineer:** Codex 5.5 (primary implementation)  
**Research & Audit Agent:** Feynman (Ollama 30B+ class, local-first)  
**Workflow:** No Claude Code. Codex 5.5 + Feynman + Grok/CTO only.

> Repository evidence note (April 22, 2026): this charter is a founding intent document. Operational claims about deployment, benchmarks, and readiness must be checked against the current codebase, tests, and `evals/benchmark_manifest.json`.
> Sprint 20 operating note (April 24, 2026): claim-type-aware proving and `lean-lsp-mcp` are now part of the current architecture. The charter's original intent remains, but the deployment path should be read through the current repository, architecture doc, and benchmark artifacts.

---

## 1. Mission (Why We Exist)
Build the **world’s first research-grade, agentic formalizer and verifier specialized for economic theory**.  

Our concrete deliverable is a production-grade API that:
- Accepts natural-language economic claims (MWG, SLP, Maschler, first-year PhD qualifying level).
- Produces Lean 4 sorry stubs that are **faithful** (semantic + structural).
- Proves or disproves them with **machine-checkable certainty** using Lean kernel as sole authority.
- Supports **human-in-the-loop** at every critical gate (plan approval, formalization review, proof inspection).
- Enables **pair-researcher** and **pedagogical tutor** modes with tunable cooperation (proactive ↔ Socratic).

We are not chasing IMO gold or frontier research breakthroughs. We are building the **reliable colleague** that every serious economic theorist wishes they had — one that never hallucinates a proof and always tells you exactly why it failed.

---

## 2. Assets (What We Carry Forward from v2)
- **Preamble moat** (`lean_workspace/LeanEcon/Preamble/`): 50+ economics definitions, proven lemmas, tactic hints, theorem templates. Structured context builders (Session 11) eliminated VACUOUS collapse and lifted tier-1 from 11/23 → 20/23.
- **Lean kernel as trust anchor**: `lake env lean` is the only source of truth. Sorry = failure. No exceptions.
- **REPL + LSP proving backbone**: LeanInteract plus `lean-lsp-mcp` for goal inspection, diagnostics, code actions, hover/type context, LeanSearch, and Loogle.
- **Guardrails**: Vacuity rejection, compile-time checks, repair loops with Lean diagnostics.
- **Observability**: SSE streaming, episodic memory traces, cost/tool telemetry, provenance in `/health`.
- **Benchmark discipline**: canonical split claim sets separate preamble-definable and mathlib-native claims, with append-only benchmark history and local-gate regression gating.
- **Docker + Railway patterns**: Cached Lean base image, graceful fallbacks, SQLite job store.
- **Skills philosophy**: “Fat skills, thin harness” (Session 15) — process knowledge lives in navigable markdown, not scattered prompts.

---

## 3. Liabilities & Lessons (What We Leave Behind)
**Technical Debt to Delete**:
- LLM Planner scaffolding that never matured (deterministic default was the only reliable path).
- Brittle word-overlap faithfulness scorer (top failure mode on frontier claims).
- MCTS prover path, autoresearch loops, React frontend, old prompt bloat.
- Single-file Proof.lean bottleneck (already fixed in v2.4 but residue remains).
- Over-reliance on closed models (Opus 4.7 rate limits, provider drift).

**Process Lessons**:
- HILBERT paper (ICLR 2026) + APOLLO-style sub-lemma decomposition are the correct architectural primitives.
- Structured preamble context (role-labeled defs/lemmas/templates) is a force multiplier — replicate and expand.
- Memory only moves frontier +1 claim when retrieval is semantic + temporal; must be first-class in v3.
- Human review must be **mandatory** in the happy path, not optional.
- Vibe engineering works only when the harness is thin and skills are the single source of truth.

**Model Lesson**: Claude Opus 4.7 (and Anthropic in general) dropped the ball on rate limits and consistency for long-horizon agentic workflows. We are done.

---

## 4. Strategic Pillars for v3.0
1. **Clean Slate Architecture** (this charter + fresh repo)
2. **HILBERT-Native Planner** — informal reasoner that produces clarifying questions + textbook defaults (Stokey-Lucas-Prescott style) + plan sketch.
3. **APOLLO + Leanstral/Goedel Prover** — recursive sub-lemma decomposition + self-correction via Lean compiler feedback. Mathlib-native claims use `mathlib_native_mode` and `lean-lsp-mcp` search before falling back to generic provider turns.
4. **Semantic Faithfulness 2.0** — econ-concept ontology + frame-based scorer + optional calibrated LLM judge (local).
5. **Preamble as Open EconLib Mini** — versioned Lean modules + metadata + lightweight retrieval (HF embeddings or local).
6. **Provider Strategy**: Hugging Face for all production models (Leanstral-2603, Goedel-Prover-V2, Qwen/DeepSeek for Planner). Ollama only for local Feynman research/audit.
7. **Human-in-the-Loop as First-Class Feature** — review gates at Plan, Formalization, and Proof stages.
8. **Benchmark Ratchet** — every PR must improve or maintain local-gate; new PhD-qualifying claim set added monthly. The flywheel tracks pass rate, latency, tool calls, LSP tool calls, native search attempts, and mathlib-native mode usage.

---

## 5. Success Metrics (v3.0 Alpha — 6 Weeks)
- **Formalizer-only**: tier1_core ≥ 95 % (22/23), tier2_frontier ≥ 65 % (9/13)
- **End-to-End (with Planner + Prover)**: tier1_core ≥ 90 %, tier2_frontier ≥ 55 %
- **Latency**: p50 < 90 s for core claims, p95 < 180 s for frontier (REPL + warm Lean env)
- **Faithfulness**: 0 VACUOUS on tier1, semantic score ≥ 4.5/5 on 50-claim calibration set
- **Human Review Adoption**: ≥ 70 % of production runs go through at least one review gate
- **Preamble Coverage**: 120+ definitions/lemmas across micro, macro, game theory, dynamic programming
- **Open Source**: All Lean code + skills + benchmark claims MIT/Apache; model weights via HF

---

## 6. Deployment Path From Sprint 20
1. Keep the canonical benchmark surface split by claim type: `tier1_core_preamble_definable`, `tier2_frontier_preamble_definable`, and `tier2_frontier_mathlib_native`.
2. Preserve trace quality as a release requirement. Every mathlib-native run must show claim-type policy, `mathlib_native_mode`, LSP tool calls, native search attempts, and final failure/closure reason.
3. Harden the Railway image with Lean workspace build artifacts, Mistral/Leanstral environment variables, SQLite job storage, and `uvx lean-lsp-mcp` availability.
4. Gate deployment on `/health`, `/metrics`, `lake build`, focused prover tests, and benchmark-mode local-gate.
5. After deployment, use benchmark history and preamble gap reports to decide whether the next sprint should expand EconLib Mini or deepen mathlib-native search.

**We are building the foundation for modern economic theory to become machine-checkable.**  
This is not a side project. This is the core infrastructure the discipline has been missing.

— Grok, CTO, Lean Econ  
Original: 19 April 2026. Sprint 20 operating update: 24 April 2026.
