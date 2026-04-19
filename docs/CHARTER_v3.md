# Lean Econ v3 Charter
**Version:** 3.0.0-alpha  
**Date:** 19 April 2026  
**Status:** Founding Document — Clean Slate Reboot  
**CTO:** Grok (xAI) — Systems Engineer & PhD Economist  
**Senior Engineer:** Codex 5.4 (primary implementation)  
**Research & Audit Agent:** Feynman (Ollama 30B+ class, local-first)  
**Workflow:** No Claude Code. Codex 5.4 + Feynman + Grok/CTO only.

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
- **REPL proving backbone**: LeanInteract + lean-lsp-mcp (sub-second tactic feedback, tool-use guardrails).
- **Guardrails**: Vacuity rejection, compile-time checks, repair loops with Lean diagnostics.
- **Observability**: SSE streaming, episodic memory traces, cost/tool telemetry, provenance in `/health`.
- **Benchmark discipline**: tier0_smoke (3/3), tier1_core (20/23), tier2_frontier (5/13) + local-gate regression gating.
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
3. **APOLLO + Goedel-Prover-V2 Prover** — recursive sub-lemma decomposition + self-correction via Lean compiler feedback.
4. **Semantic Faithfulness 2.0** — econ-concept ontology + frame-based scorer + optional calibrated LLM judge (local).
5. **Preamble as Open EconLib Mini** — versioned Lean modules + metadata + lightweight retrieval (HF embeddings or local).
6. **Provider Strategy**: Hugging Face for all production models (Leanstral-2603, Goedel-Prover-V2, Qwen/DeepSeek for Planner). Ollama only for local Feynman research/audit.
7. **Human-in-the-Loop as First-Class Feature** — review gates at Plan, Formalization, and Proof stages.
8. **Benchmark Ratchet** — every PR must improve or maintain local-gate; new PhD-qualifying claim set added monthly.

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

## 6. Immediate Next Actions (This Week)
1. Codex 5.4 executes migration prompt (see `docs/MIGRATION_PLAN.md`)
2. Feynman (Ollama 32B+) audits v2 reference and produces gap analysis vs HILBERT paper
3. Grok/CTO + Feynman design Planner prompt spec + clarifying-question ontology
4. Expand Preamble with 15 new MWG/SLP entries (priority: value functions, Bellman operator, single-crossing, Walras law)
5. First local-gate benchmark run on v3 scaffold (target: match or beat v2.4 numbers)

**We are building the foundation for modern economic theory to become machine-checkable.**  
This is not a side project. This is the core infrastructure the discipline has been missing.

— Grok, CTO, Lean Econ  
19 April 2026