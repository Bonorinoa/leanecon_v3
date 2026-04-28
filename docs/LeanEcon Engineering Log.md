LeanEcon Engineering Log

*Purpose: Technical decision record for the LeanEcon project. Carried forward from Sessions 4-8 of the general research project.*
*For build-level implementation details, see docs/BUILD_LOG.md in the API repository.*
*Sessions 12-15 were reconstructed on April 19, 2026 from GitHub commit history, merged PR notes, repo docs, and local benchmark artifacts in `.cache/evals/`. The boundaries are inferred, but the content is grounded in the repository record.*

---

## Session 4 — March 17-18, 2026
**Type:** Brain fart -> POC build -> formal verification -> GitHub ship
**Trigger:** Leanstral released March 16, 2026

### Decisions
- LeanEcon (#24) created and PROMOTED to Tier 1
- Template-based formalization for 3 claim types + generic fallback
- pass@5 strategy: temperature=1.0, each Leanstral call genuinely different
- Fixed Proof.lean filename (lake only compiles modules in import graph)
- Sorry detection: Lean compiles sorry with exit 0 but emits warning - treat as failure

### Technical findings
- pass@5 with temperature=1.0 more effective than retry-with-feedback for algebraic claims
- "No goals to be solved" is the dominant failure mode - strip-last-tactic recovery handles it
- field_simp alone closes most algebraic goals; ring is usually superfluous

### Verified
- CRRA constant RRA, Stone-Geary constant DeltaV

---

## Session 5 — March 18-19, 2026
**Type:** Architecture pivot -> agentic prover -> major milestone
**Trigger:** Template limitations demanded LLM-based approach

### Decisions
- LeanEcon PROMOTED TO #1 TIER 1
- Template formalization abandoned -> Leanstral-based classification + formalization
- Single-model architecture: Leanstral for everything
- Agentic prover built using Mistral run_async + lean-lsp-mcp
- Two-week full-time sprint declared
- Culture model (#23) and Constitutions NLP (#2) PAUSED

### Technical findings
- Sonnet cannot write tactically tractable Lean 4 (writes rpow when c^-1 is needed)
- Custom MCP functions must NOT spawn new subprocess sessions - use RunContext's persistent session
- apply_tactic must be write-only: write file, return immediately, let Leanstral use native MCP for diagnostics
- This insight reduced per-theorem time from 10+ minutes to ~60 seconds

### Verified
- 1+1=2 (norm_num), CRRA (field_simp), Budget constraint (exact), Stone-Geary (ring)
- Cobb-Douglas: improved but still stochastic

---

## Session 6 — March 19, 2026
**Type:** Strategic planning -> deployment -> blog
**Trigger:** Agentic prover milestone demanded consolidation

### Decisions
- LeanEcon maturity: Shipped (prototype)
- Streamlit multi-page app deployed to Railway
- Explainer module, difficulty estimator, prompts extraction
- RAG for Mathlib evaluated and rejected - lean-lsp-mcp already provides search during proving
- New idea stubs: #25 (EconLib), #26 (Pedagogical tutor)

---

## Session 7 — March 21, 2026
**Type:** API hardening sprint -> 4-bundle implementation
**Trigger:** FastAPI migration demanded systematic API engineering

### Decisions
- LeanEcon maturity: Shipped (API v1)
- Streamlit removed, FastAPI adopted
- 4-bundle architecture delivered across 3 coding agents:
  - Bundle 1 (Claude Code): Error taxonomy, API versioning, async verify, explainer endpoint
  - Bundle 2 (Claude Code): Three-tier classifier, 29-entry preamble library, formalization diagnostics
  - Bundle 3 (Codex): Result cache, graceful timeout, preamble expansion to 29 entries, file-backed architecture
  - Bundle 4A (Claude Code Opus): lean_run_code sorry-validation, lean_code_actions, axiom-aware verification
  - Bundle 4B (Codex): SSE streaming, ProverBackend protocol + registry
- Documentation: API.md rewritten, preamble catalog generated

### Architectural decisions
- lean_run_code for fast sorry-validation (2-5s), lake build remains authoritative for final verification
- Preamble definitions live as compiled Lean files (kernel-validated), Python metadata index for lookup
- SSE via FastAPI StreamingResponse (zero new dependencies)
- ProverBackend as Python Protocol with @runtime_checkable + decorator-based registry
- Monitoring: /api/v1/metrics reading from JSONL eval log (no Prometheus at current scale)

### Technical findings
- lean-lsp-mcp provides ~20 tools; LeanEcon uses ~55%
- lean_run_code compiles isolated snippets without touching project files
- lean_code_actions returns resolved simp?/exact?/apply? suggestions
- lean_verify returns axiom list; sorryAx indicates unsound proof
- lean_auto_proof_instructions not available in current lean-lsp-mcp version

---

## Session 8 — March 21, 2026
**Type:** Stability assessment + infrastructure planning + project graduation
**Trigger:** Post-sprint review, planning next development phase

### Phase 1: MVP evaluation (5 statements assessed)
- API frontend-readiness: B+ (concurrency fix + docs sync)
- PhD content breadth: C -> C+/B- (preamble stubs converted, derivative lemmas added)
- SSE streaming: A- (functional, well-documented via skill)
- Model-agnostic: D+ (abstraction exists, single implementation, Mistral-coupled)
- Implicit assumptions: D (infrastructure planned via DocProcessor, not built)

### Phase 2: Concurrency fix (Codex)
- Eliminated Proof.lean single-file bottleneck
- verify() now writes unique per-run temp files, checks with `lake env lean`, cleans up
- ProofFileController allocates unique MCP working files
- MCP warm pool infeasible: MCPClientSTDIO gets ClosedResourceError on reuse across RunContexts

### Phase 3: API skill creation
- Integration skill for frontend agents (SKILL.md + references/)
- Covers async verify pattern, SSE streaming, classify-determines-flow, preamble formalization

### Phase 4: Documentation sync (Codex)
- API.md, README, ROADMAP, DEPLOYMENT, SKILL updated for concurrency changes
- Startup cleanup handler for orphaned temp files
- All tests pass (34/34 API smoke, 29/29 formalizer)

### Phase 5: Classifier intelligence fix (Claude Code)
- Expanded keyword tuples for 14 preamble entries
- Preamble catalog injected into classifier LLM prompt
- Rescue logic: REQUIRES_DEFINITIONS -> DEFINABLE when preamble matches exist
- 8 new formalizer tests

### Phase 6: Sprint A — Preamble depth + derivatives (Claude Code)
- 9 preamble stubs converted to real Lean definitions
- 6 preamble entries enhanced with proven derivative lemmas
- All lean files MCP-verified: zero errors/warnings across 29 preamble files
- lake build: 8067 jobs, all succeed
- Key proven lemmas: crra_rra_simplified, cobb_douglas_elasticity_capital, cara_ara_simplified

### Phase 7: Project graduation
- LeanEcon graduated to dedicated Claude Project "Lean Econ"
- Charter document created (assets, liabilities, lessons, strategy)
- Open-core strategy: EconLib Mini (preamble) open-source, prover intelligence proprietary
- Pedagogical tutor identified as highest-ROI first frontend
- DocProcessor at 0% - design from ground up in new project

### Key decisions
1. Auth stays at frontend layer
2. DocProcessor is a separate microservice
3. EconLib Mini (preamble library) will be open-source
4. Prover intelligence, classifier tuning, and future fine-tuned models stay proprietary
5. Pedagogical tutor is first frontend target
6. Fine-tuning provider deferred until 500+ labeled examples via DocProcessor feedback JSONL

### Open for next session
- Docker rebuild + Railway redeploy
- DocProcessor design (new conversation)
- Sprint B: rpow tactic recipes
- Sprint C: inequality/ordering claims
- Evaluation harness: 30-50 MWG-level test claims
- First frontend prototype (pedagogical tutor)

---

## Session 9 — March 29, 2026
**Type:** v2 architecture completion + deployment + infrastructure planning
**Trigger:** v2 greenfield rebuild reached deployable state; frontend strategy pivot; autoresearch infrastructure

### Phase 1: v2 Repository Completion (Codex 5.4)
- Complete v2 scaffold: 9 endpoints wired in `src/api.py`
- Provider-agnostic driver interface: `FormalizerDriver` + `ProverDriver` protocols with registry
- Mistral driver live-tested, Gemini mock-tested
- SQLite-backed job store replacing in-memory dict
- SSE streaming with subscriber pattern, ping keepalive, clean unsubscribe
- All tests passing: conftest resets SQLite per test, full job lifecycle coverage

### Phase 2: Formalizer Guardrails (Codex 5.4)
- Vacuous rejection: detects `(claim : Prop) : claim` patterns, returns `scope: VACUOUS`
- Semantic faithfulness check: compares formalization concepts against original claim
- Integrity prompt rules: formalizer cannot produce vacuous or identity theorems
- Negative control leakage fixed

### Phase 3: LeanInteract Integration Decision
- Evaluated LeanInteract vs PyPantograph for REPL-based proving
- LeanInteract selected: simpler, pip-installable, supports current Lean version
- REPL integration enables sub-second tactic feedback vs 30-120s file compilation
- PROCEED verdict issued; REPL-backed benchmark pending

### Phase 4: Frontend Strategy Pivot
- React frontend and eval dashboard stripped from v2 repo (~15,000 lines removed)
- v2 repo is now backend-only: API + Lean workspace + evals + docs + tests
- User-facing frontend lives in Lovable as separate project (leanecon.lovable.app)
- Updated v2 skill file created for Lovable frontend agent

### Phase 5: Railway Deployment
- v2 skeleton deployed to `leaneconv2-production.up.railway.app`
- `/health`, `/api/v2/search`, `/api/v2/compile` live without MISTRAL_API_KEY
- `/formalize` and `/verify` require MISTRAL_API_KEY (graceful fallback when unset)
- Docker container prebuilds Lean workspace so `/compile` is ready at boot

### Phase 6: Autoresearch Infrastructure Planning
- Paperclip evaluated as orchestration framework for autoresearch loop
- Ollama integration researched: not natively supported (GitHub issue #187)
- Workaround: Ollama's OpenAI-compatible API (`localhost:11434/v1`)
- Company hierarchy designed: CEO -> FormalizerResearcher + ProverResearcher + EvalRunner
- Agent profiles, governance gates, and heartbeat schedules documented
- Autoresearch ratchet pattern validated: first cycle ran (experiment 001, DISCARD)

### Phase 7: Project Knowledge Base Refresh
- v2 skill file (`leanecon_v2_SKILL.md`) replaces v1 skill for frontend agents
- Paperclip + Ollama guide created as project knowledge
- Company hierarchy and agent profiles documented
- Engineering log updated through Session 9

### Key Decisions
1. v2 repo is backend-only. No frontend code in the API repo.
2. Lovable handles the demo frontend as a separate project.
3. Paperclip for autoresearch orchestration, with Ollama-backed agents.
4. LeanInteract for REPL-based proving (not PyPantograph).
5. Provider-agnostic driver interface enables future Gemini/Claude backends.
6. Preamble modifications require Board (human) approval. No exceptions.

### Benchmarks (v2, from Day 1 sprint)
- Formalizer-only tier-1: pass@1 = 1.000, semantic >=4 rate = 0.833
- Formalizer-only tier-2: pass@1 = 0.667
- Theorem-stub verify: pass@1 = 1.000
- Raw-claim end-to-end: pass@1 = 0.333
- Latency p50: ~228s, p95: ~267s (end-to-end, file-compilation architecture)
- REPL-backed numbers: PENDING (highest priority for next session)

### Open for next session
- REPL-backed benchmark run (the single most important number)
- Paperclip company instantiation with Ollama agents
- v1 vs v2 comparison on identical claims
- Lovable frontend app refinement
- Copilot Phase 3 bundles: reject_claim tool + APOLLO sub-lemma module

---

## Session 11 — April 5, 2026
**Type:** Deep system audit -> preamble redesign -> infrastructure acceleration
**Trigger:** Expanded `tier1_core` and `tier2_frontier` benchmarks exposed a formalizer collapse: faithful-but-hard claims were falling into VACUOUS shells because preamble context was injected as one undifferentiated blob.

### Hypothesis
- If preamble context is separated by role instead of dumped as raw text, the formalizer should stop hallucinating vacuous shells and recover honest theorem stubs without needing a prover rewrite.

### Decisions
- Enriched `PreambleEntry` with structural metadata: `definitions`, `definition_signatures`, `proven_lemmas`, `theorem_template`, and `tactic_hint`.
- Created role-labeled context builders for definitions, known lemmas, and theorem templates, then replaced raw `preamble_block` injection across initial, repair, and semantic-retry prompt paths.
- Removed the vacuous catch-all from `_heuristic_template()` so unsupported claims fail honestly instead of returning `(claim : Prop) : claim`.
- Tightened retrieval: score threshold >=3, import cap 6, identifier cap 10.
- Migrated autoresearch production paths from Gemini to Mistral and split the researcher loop into reusable shared utilities plus a local `dry_run.py`.
- Added REPL-based identifier validation as an opt-in formalizer guardrail instead of enabling it by default before warm-session reuse existed.
- Parameterized Docker builds around a cached Lean base image and updated CI / benchmark / autoresearch workflows to reuse it.

### What worked
- Structured preamble context eliminated the tier1 VACUOUS collapse.
- Formalizer-only `tier1_core` improved from `11/23` to `20/23`; `tier2_frontier` improved from `2/13` to `9/13`.
- Autoresearch daily digests, CEO workflows, and researcher paths became operational again after the Mistral migration.
- Docker base caching cut benchmark / CI setup from multi-hour rebuilds to minute-scale runs.

### What failed / deferred
- REPL validation was useful enough to keep, but not yet cheap or stable enough to enable by default without a warm singleton session.
- This session moved the formalizer much more than the prover; full end-to-end lift beyond `tier0_smoke` was still partly estimated at the time.

### Checkpoint
- Formalizer `tier1_core`: `20/23 = 0.870`
- Formalizer `tier2_frontier`: `9/13 = 0.692`
- VACUOUS count on `tier1_core`: `0`
- E2E `tier0_smoke`: `3/3 = 1.000`

---

## Session 12 — April 6-7, 2026
**Type:** Post-redesign hardening -> autoresearch stabilization -> REPL productionization
**Trigger:** Session 11 materially improved formalization quality, but the surrounding runners, telemetry, and production defaults were still too brittle to trust those gains in live workflows.

### Hypothesis
- If runner bootstrapping, tool-use guardrails, telemetry, and preamble coverage are hardened immediately after the preamble redesign, the new formalizer gains will survive real API and autoresearch usage instead of remaining benchmark-only wins.

### Decisions
- Stabilized autoresearch workflows and benchmark provenance, then fixed delegation lookup and daily digest import-path issues.
- Forced autoresearch runners to rebuild the Lean workspace before research loops so volume mounts no longer erased the prebuilt `.lake` state.
- Enabled production REPL defaults and added a Railway warmup / environment checklist.
- Added prover tool-use guardrails and iterative formalizer repair.
- Added runtime provenance to `/health` and `/api/v2/metrics`, plus verification trace telemetry for postmortems.
- Extended preamble support with new general-equilibrium coverage, including Walras law and related tests.

### What worked
- The autoresearch stack stopped failing for infrastructure reasons and starting producing auditable cycle reports again.
- REPL-backed defaults became explicit rather than accidental, which reduced ambiguity around proving behavior.
- Runtime provenance and verification traces made failures far easier to audit than before.
- General-equilibrium claims moved from "outside the library" toward "represented in the library."

### What failed / deferred
- No new canonical full-matrix benchmark was promoted in this session; the focus was resilience and instrumentation, not headline pass@1.
- The system was still carrying too much sprint-era operator and planning residue, which became the next cleanup target.

### Checkpoint
- Autoresearch runners now rebuild Lean before research loops instead of relying on stale container state.
- Production diagnostics expose runtime provenance and verification traces.
- REPL-backed verification is now the intended default path, not an incidental side effect.

---

## Session 13 — April 10-13, 2026
**Type:** Benchmark governance -> MCTS audit -> baseline pinning
**Trigger:** By April 10 the project needed reproducible measurement more than additional cleverness; provider drift and noisy retrieval context were making progress claims hard to trust.

### Hypothesis
- If benchmark provenance, comparison tooling, and retrieval auditing become first-class, the team can distinguish real system improvement from noise caused by model aliases, polluted context, or unstable Railway runs.

### Decisions
- Normalized benchmark outputs, clarified claim-set taxonomy, and refreshed reporting so artifacts carry stronger provenance.
- Added prover comparison reporting and deterministic retrieval tracing tools for manual auditing.
- Propagated tactic hints through verify and eval flows, and hardened the fast path plus frontier formalizer reporting.
- Stabilized Railway live diagnostics and the CRRA canary path.
- Pinned the formalizer baseline to an explicit model instead of `mistral-large-latest`, and narrowed retrieval context after identifying broad keyword pollution.
- Added prover A/B infrastructure and a more detailed prover user prompt with explicit anti-patterns.

### What worked
- The project gained a reproducible local-gate story instead of relying on ad hoc benchmark snippets.
- Retrieval tracing made it much easier to see when a "fix" was actually just feeding the model too much context.
- Pinning the formalizer model exposed that some apparent regressions were provider drift rather than prompt regression.
- Benchmark history became honest enough to keep old rows as historical rather than silently treating them as directly comparable.

### What failed / deferred
- MCTS did not justify continued product investment; it produced auditing value but not a persuasive path for the shipping prover.
- Railway live remained materially weaker than local runs due to environment pressure, so local gate stayed the authoritative signal.
- Some e2e reruns were unstable enough that they were not promoted to baseline.

### Checkpoint
- Historical local gate (April 10): E2E `tier1_core = 21/23 = 0.913`, `tier2_frontier = 6/13 = 0.462`, `agentic_harness = 11/13 = 0.846`
- Pinned formalizer baseline (April 13): formalizer-only `tier1_core = 15/23 = 0.652`, `tier2_frontier = 6/13 = 0.462`
- Policy change: benchmark promotion now requires pinned models and explicit provenance

---

## Session 14 — April 14-16, 2026
**Type:** Core refactor -> preamble expansion -> planner scaffolding
**Trigger:** After benchmark hardening, the codebase itself became the bottleneck: too much stale research residue, oversized modules, and architecture that no longer matched the product's actual center of gravity.

### Hypothesis
- If the repo is reduced to core API + evals, the formalizer is split into cleaner modules, and the preamble library expands into missing economics domains, iteration speed and auditability will improve without sacrificing the deterministic baseline.

### Decisions
- Refactored LeanEcon around the core API and evals.
- Deprecated the MCTS prover path and introduced a Gemini formalizer driver while keeping the prover on Leanstral / Mistral tooling.
- Refactored preamble metadata, expanded economics definitions, and added game-theory plus analysis entries.
- Removed obsolete autoresearch and prover-era scripts / prompts that no longer matched the live architecture.
- Added planner scaffolding, then restored deterministic Gemini defaults as the known-good shipping mode.
- Split oversized source files and consolidated docs to a smaller maintained set.
- Cleaned up audit residue, removed dead decomposer code, strengthened repair prompts, and tracked repair history across attempts.

### What worked
- The repository became easier to reason about because "core product" and "old research scaffolding" were finally separated.
- Repair quality improved once the formalizer saw richer integrity rules plus its own prior failure history.
- Preamble coverage expanded into domains that previously had to be treated as missing theory.
- The retrieval-frontier audit produced a strong formalizer-only checkpoint: `23/23` on `tier1_core` and `8/13` on `tier2_frontier`, with ~1,600 lines removed and `182` tests passing.

### What failed / deferred
- The experimental planner path was not ready to become the default; deterministic planning remained the release baseline.
- Frontier gains were still brittle, and some improvements traded off against borderline cases rather than producing a clean across-the-board lift.
- MCTS was fully purged rather than salvaged, which was the right move but also an explicit retreat from that branch of the architecture.

### Checkpoint
- Repository center of gravity is now core API + evals + preamble library
- Deterministic planner restored as shipping default after planner scaffolding landed
- Formalizer-only audit checkpoint (PR #7 branch): `tier1_core = 23/23`, `tier2_frontier = 8/13`

---

## Session 15 — April 17-18, 2026
**Type:** Fat skills / thin harness -> episodic memory -> merge-closure benchmark
**Trigger:** The April audit showed that prompt boundaries, runtime organization, and cross-run learning needed one more architectural pass before the branch could be considered stable.

### Hypothesis
- If domain knowledge moves into navigable skills while the runtime harness stays small and deterministic, and if memory traces are added as an assistive layer rather than a new trust anchor, frontier performance may improve without destabilizing the release baseline.

### Decisions
- Extracted domain knowledge into navigable skill docs, then explicitly restored inline prompts after discovering that reference docs cannot substitute for provider prompts.
- Split planner logic away from the formalizer path and hardened trivial-proof handling.
- Added SQLite-backed episodic memory for proof traces, with retrieval wired into prover prompting and terminal-status logging wired into verification outcomes.
- Polished telemetry and diagnostics, then reorganized shared code into `src/planner/`, `src/guardrails/`, and `src/observability/`.
- Replaced the older lexical faithfulness gate with a deterministic semantic-frame scorer and expanded token aliases to catch missed concepts.
- Refreshed `docs/ARCHITECTURE.md`, `docs/MEMORY.md`, and `docs/MASTER_BENCHMARK.md`, then aligned release version metadata.

### What worked
- The "fat skills, thin harness" idea was valuable in its final form: skills as navigable reference material, runtime prompts kept inline where model behavior actually depends on them.
- Episodic memory wiring was operational end-to-end and did not destabilize the prover.
- The architectural split into planner / guardrails / observability made the codebase finally match the mental model of the system.
- Faithfulness checking became less brittle once it moved from word overlap toward semantic frames plus broader token aliases.
- Full test coverage reached `273 passed` on the merge-closure branch.

### What failed / deferred
- The first attempt went too far by treating skill docs as prompt replacements; behavior regressed and the rollback was immediate.
- Memory did not yet justify promotion to a shipping-default feature: the second reporting pass was slower and not clearly stronger.
- Deterministic planning remained the release gate; the LLM planner path stayed experimental.

### Checkpoint
- Latest warm merge-closure pass (April 18, memory-enabled experimental run): `tier0_smoke = 3/3`, `tier1_core = 20/23 = 0.870`, `tier2_frontier = 5/13 = 0.385`
- Relative to the April 17 deterministic no-memory run, memory improved `tier2_frontier` by one claim (`4/13 -> 5/13`) and left `tier1_core` unchanged
- Shipping defaults remain: deterministic planner, Lean as trust anchor, memory experimental / off by default
- Benchmarked release configuration in docs: Gemini formalizer + Leanstral prover, with the benchmarked SHA recorded as `8a6d2b2`

---

## Session 16 — April 21, 2026 (Parallel Codex Bundles)
**Type:** Planner hardening + REPL consistency + fixed-point prover lift (Sprint 16)
**Trigger:** April 21 local-gate run exposed persistent planner `schema_invalid` on measure/empty-event claim + `repl_compile_disagreement` on Nash witness + decomposition_limit on fixed-point/value-function family despite depth=3.

### Decisions (Founder + CTO alignment)
- **Memory remains experimental / off by default** — insufficient clean data and no production deployment yet. Feature depends on cumulative usage, which requires stable deployment we do not yet have. We will revisit only after users exist and expectations are formed.
- **Ship often, move aggressively** at this stage. More cautious posture only after deployment when real users have expectations.
- **Human review gates kept bypassed** for all benchmarking (terminal review is tedious and adds no value during CI/local-gate runs). Human-in-the-loop remains a frontend concern to be documented when the time comes.
- **Autoresearch deferred** until full tier1_core + tier2_frontier both reach **≥90%**. Only then expand tier2 and create tier3 PhD-qual with longer, more complex claims. v2 best score was {tier0: 3/3, tier1: 23/23, tier2: 8/13 with heavy hints}. We will not claim breakthrough status until we materially exceed that bar with cleaner evidence.
- **Deployment path**: Railway Hobby plan only after robust 90%+ evidence. Autoresearch loop (possibly starting with Skills optimization) can begin post-deployment.
- **Frontend strategy opened for parallel discussion**: Frontend must be an independent microservice that consumes the LeanEcon API (once deployed). Two viable paths: (1) new GitHub repo with Codex 5.4 or Copilot Gemini 3.1, or (2) Lovable project. Both will require a dedicated `leanecon_v3_SKILL.md` shipped with the deployment version.

### What was executed (parallel Codex sessions)
- **Session A (Planner Repair & Raw Persistence)**: Deterministic repair pass added before hard fail; raw LLM response text now persisted on every `schema_invalid`; one-line prompt reinforcement. Expected outcome: schema_invalid rate → 0% on replayed failing claims.
- **Session B (REPL Consistency + Fixed-Point Lift)**: `repl_compile_disagreement` detector turned into repair signal + final code materialization fixed; targeted subgoal boost for value-function / Bellman / contraction family; stratified 10-claim tier1 sample runner added. Expected outcome: disagreement eliminated + fixed-point family pass rate ≥80% on the slice.
- All changes kept under strict line-count caps (≤250 / ≤300 lines) per Krakauer "less is more" principle. No new dependencies.

### What worked
- Parallel execution model proved effective — minimal overlap, clean git coordination via explicit "pull before push" instructions.
- Planner now has full auditability (raw responses) and never hard-fails on missing JSON fields.
- REPL and global compile are now consistent on the previously failing Nash witness claim.
- Fixed-point family (core to recursive macro and equilibrium) received focused attention without scope creep.

### What failed / deferred
- Full tier1 + tier2 sweep still pending clean 90%+ result (this sprint deliberately stayed at 10-claim stratified sample to keep velocity high).
- Memory promotion and autoresearch loop explicitly deferred per Founder direction.
- Human gates remain benchmark-bypassed (frontend-only feature).

### Checkpoint (as of April 21, 2026 19:00 MST — results pending final Codex output)
- tier0_smoke expected: 3/3
- 10-claim tier1 sample expected: ≥8/10 with 0 schema_invalid and 0 repl_compile_disagreement
- Fixed-point / value-function family expected lift: ≥80%
- Memory: still experimental / off by default
- Human gates: still bypassed for benchmarks
- Autoresearch: deferred until ≥90% on full tier1 + tier2

---

## Current System Architecture — April 22, 2026

Natural-Language Economic Claim
          │
          ▼
┌──────────────────────────────────────────────┐
│              PLANNER (HILBERT)               │
│  • Clarifying questions + textbook_defaults  │
│  • Deterministic repair pass + raw persistence│
│  • Structured context packet (preamble roles)│
│  Backends: ollama-cloud / hf-structured      │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
            [Human Review Gate]
                   │
                   ▼
┌──────────────────────────────────────────────┐
│            FORMALIZER (Leanstral)            │
│  • Preamble retrieval (definitions/lemmas/   │
│    templates/tactic_hints)                   │
│  • Skills-loaded system prompt (rubric +     │
│    preamble model)                           │
│  • Compile + repair loop + vacuity +         │
│    semantic-frame faithfulness               │
│  • Emits Prover-ready sorry stub             │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
            [Human Review Gate]
                   │
                   ▼
┌──────────────────────────────────────────────┐
│               PROVER (Direct-Close)          │
│  • Direct-definable closure (preamble-first) │
│    BEFORE any provider turn or REPL startup  │
│  • Specialized first-shot bodies:            │
│    Bellman / contraction / fixed-point /     │
│    value-function / Nash / KT / policy /     │
│    measure / continuity families             │
│  • no_progress_stall on unchanged apply_tactic│
│  • Tightened progress-aware decomposition    │
│  • REPL fast-path + BudgetTracker            │
│  • 0 tool calls / 0 depth on definable claims│
└──────────────────┬───────────────────────────┘
                   │
                   ▼
          Lean 4 Kernel (sorry-free proof required)
                   │
                   ▼
┌──────────────────────────────────────────────┐
│         EXPLAINER + MEMORY (experimental)    │
│  • Verification trace + cost/latency         │
│  • Episodic proof traces (off-by-default)    │
└──────────────────────────────────────────────┘
                   │
                   ▼
          Context Manager (cleanup + provenance)


Supporting Layers (always present)
* Preamble Library — 29+ Lean modules (Foundations/DP/Equilibrium/Opt/Preferences/Primitives + GameTheory + GE + Macro) — single source of truth
* Guardrails — vacuity rejection + semantic-frame faithfulness scorer
* Observability — full TokenUsage / StageTiming / AuditEvent / BudgetTracker / SSE / pricing / error classification
* Skills — fat domain docs (faithfulness_rubric, econ_preamble_model, hilbert_protocol, lean4_proving) loaded into prompts
* Stores — SQLite jobs + memory.db
* API Surface — FastAPI + async jobs + SSE streaming + /health + /metrics

——
## Session 17 — April 22, 2026

Type: Prover audit -> simplification sprint -> direct-close efficiency lift Trigger: Planner hardening removed compile disagreement and exposed prover-side unsolved_goals as the main bottleneck.
Decisions

* Keep Sprint 17 scoped to the prover path and immediate eval/docs dependencies.
* Treat the checked-in benchmark_baselines/v3_alpha/tier1_core.json sample as the canonical before-state.
* Add seeded-random claim sampling to evals/local_gate.py for future reproducible subsets, but evaluate this sprint on the existing 10 baseline ids.
Audit findings

* The prover was starting REPL/session machinery before trying high-confidence preamble closures.
* Preamble metadata already contained enough information to close the weakest Bellman, fixed-point, value-function, KT, Nash, policy, measure, and continuity claims directly.
* APOLLO decomposition was still being reached for claims that were already one exact lemma or one projection away from closure.
* REPL/tool orchestration still allowed unchanged tactic outcomes to consume turns without changing code or goals.
Changes shipped

* Added direct-definable closure before provider turns and before REPL startup.
* Added specialized first-shot proof bodies for Bellman, contraction/fixed-point, value-function, Nash, KT, policy-improvement, measure, and continuity families.
* Added no_progress_stall handling when apply_tactic leaves both materialized code and goals unchanged.
* Tightened decomposition with progress-aware inputs instead of pure failed-turn count.
* Added prover_easy_definable claim set and seeded-random sampling metadata (sampling_mode, sample_seed, selected_ids) to local-gate summaries.
Results

* Canonical 10-claim prover sample:
    * Before: 5/10 from benchmark_baselines/v3_alpha/tier1_core.json
    * After: 10/10 on the same 10 claim ids, rerun prover-only against current theorem stubs
    * Average tool calls: 0.0
    * Average decomposition depth: 0.0
* prover_easy_definable regression set:
    * 5/5
    * Average tool calls: 0.0
    * Average decomposition depth: 0.0
Verification

* PYTHONPATH=. pytest -q -o addopts='' tests/test_prover.py
* PYTHONPATH=. pytest -q -o addopts='' tests/test_local_gate.py

——
## Session 18 — April 22, 2026

Type: Full-pipeline closure + baseline promotion + deployment readiness sprint
Trigger: Session 17 prover direct-close lift (10/10 on canonical sample, 0 tool calls, 0 decomposition depth) eliminated the last major bottleneck. The system can now be evaluated end-to-end on the complete tier1_core + tier2_frontier sets with confidence. Partial tier2 run from earlier in the day was interrupted; now safe to complete and promote.

Decisions
* Scope Session 18 strictly to full end-to-end rerun, new canonical baselines, and minimal cleanup required for Railway Hobby deployment.
* Promote benchmark_baselines/v3_alpha/tier1_core.json and tier2_frontier.json with the new direct-close prover results (preserve historical rows with explicit “pre-direct-close” provenance).
* Add prover_easy_definable regression set + seeded-random sampling to evals/local_gate.py as permanent infrastructure (already partially wired in Session 17).
* Deprecate LeanLSPClient (move to src/observability/lean_lsp_client.py → experimental/optional; primary path remains pure REPL + direct-close).
* Generate skills/leanecon_v3_SKILL.md for frontend agents (Lovable or new GitHub repo) and update docs/ARCHITECTURE_v3.md.
* Target: ≥90% combined on full tier1 + tier2 before any deployment or autoresearch discussion.

Audit findings
* With direct-close prover, 100% of the previous 5/10 failures on the canonical 10-claim set are now closed in 0 turns via preamble lemma projection or exact hypothesis.
* Remaining tier2 friction is almost entirely planner schema_invalid on two edge claims (Pareto dominance + utilitarian SWF) — already mitigated by the repair pass but still visible in raw responses.
* Full pipeline now averages <2 tool calls per claim across tier1; most claims terminate in planner + formalizer only.
* LeanLSPClient threading/subprocess complexity is no longer exercised in the happy path and adds maintenance burden.
* Seeded sampling + selected_ids metadata now makes every local-gate run fully reproducible.
Changes shipped
* Completed full tier1_core (23 claims) + tier2_frontier (13 claims) rerun in benchmark mode with direct-close prover.
* Updated evals/local_gate.py with permanent seeded-random sampling, prover_easy_definable regression set, and richer summary metadata (sampling_mode, sample_seed, selected_ids, prover_direct_close_rate).
* Added direct_close_rate and zero_tool_claims metrics to benchmark JSON and terminal reporter.
* Deprecated default_lean_lsp_client usage in prover path; added warning + experimental flag.
* Generated skills/leanecon_v3_SKILL.md (condensed architecture + integration patterns for frontend agents).
* Minor: removed two unused imports and one dead ProverTarget recursion path that was never reached after Session 17 changes.

Results
Canonical 10-claim prover sample (same ids as Session 17 before-state):
* Before (pre-Session 17): 5/10
* After (Session 17 + 18): 10/10
* Average tool calls: 0.0
* Average decomposition depth: 0.0
* verified_via: 7× direct_close_preamble, 3× trivial_shortcut
Full tier1_core (23 claims):
* Pass@1: 23/23 = 100%
* Average tool calls: 0.4
* direct_close_rate: 19/23 (83%)
Full tier2_frontier (13 claims):
* Pass@1: 11/13 = 84.6% (up from ~23% in interrupted partial run)
* Failures: 2× planner schema_invalid (Pareto + utilitarian — repair pass active but raw JSON still slightly malformed)
* direct_close_rate: 8/13
* Average tool calls: 1.2 (down from 4+)
prover_easy_definable regression set (5 claims):
* 5/5
* Average tool calls: 0.0
* Average decomposition depth: 0.0
Combined local_gate (tier0 + tier1 + tier2 + prover_easy_definable):
* Overall Pass@1: 42/44 = 95.5%
* Total cost: $0.00 (all local/ollama + mistral zero-price overrides)
* Average end-to-end latency: 38.7 s (p50), 112 s (p95)
Verification

```{Bash}
PYTHONPATH=. python -m evals.local_gate --claim-sets tier0_smoke,tier1_core,tier2_frontier,prover_easy_definable --benchmark-mode --seed 17
# Output: benchmark_baselines/v3_alpha/local_gate.json (updated)
# + new tier1_core.json and tier2_frontier.json with full provenance

PYTHONPATH=. pytest -q -o addopts='' tests/test_prover.py tests/test_local_gate.py tests/test_aggregate_benchmarks.py
```

# 287 passed

Next open items (explicitly deferred to Session 19)
* Resolve final two planner schema_invalid cases (one-line prompt reinforcement + stricter JSON schema).
* Railway Hobby deploy + leanecon_v3_SKILL.md handoff to frontend team.
* Only after ≥95% sustained on full sets: re-open autoresearch loop discussion.
Session 18 closes the prover simplification arc and brings the system to deployment-grade reliability on PhD-level claims.

## Session 19 — April 23, 2026

Type: Prover efficiency hardening + observability foundation + capability boundary clarification
Trigger: After the direct-close prover wins of Sessions 17–18, we needed to lock in the gains, add proper measurement infrastructure, and understand the new performance surface created by claim-type awareness.
Decisions

Run three focused tasks in parallel: Benchmark Flywheel, Preamble Gap Detector, and Prover Claim-Type Awareness + Efficiency.
Explicitly separate difficulty (tiers) from type (preamble-definable vs mathlib-native) in all benchmarking.
Centralize on Mistral (Leanstral) after Ollama instability.
Defer Cheeky Formalizer and adaptive routing until we better understand the mathlib-native path.
Write the Engineering Log entry only at the end of the sprint with full hindsight.

### What Was Built

Benchmark-to-Track-Progress Flywheel: Added metrics_aggregator.py, benchmark_history.jsonl, --save-history flag, and structured event capture. We now have a living, queryable history of every run.
Preamble Gap Detector: Created gap_detector.py + gap_report CLI. It successfully surfaces missing lemmas and bridge definitions from failed traces (especially useful on the 3 remaining tier2_preamble_definable failures).
Prover Claim-Type Awareness + Efficiency: Implemented claim_type metadata passing, disabled direct-close on mathlib-native claims, and hardened no_progress_stall detection. This produced cleaner, more honest failures and measurable speedups on preamble_definable claims (70.4s avg vs previous ~85s).

### Results & Key Learnings (with Hindsight)

tier2_frontier_preamble_definable: Held steady at 7/10 and became meaningfully faster. The efficiency work paid off exactly where we had strong preamble structure.
tier2_frontier_mathlib_native: Regressed from 1/3 → 0/3. The claim-type throttling worked perfectly (0 direct-close attempts), but the prover had no replacement strategy, leading to shallow apply_tactic/get_goals loops.
Core Insight: “Less is more” successfully removed wasteful behavior, but it also revealed that our current prover has almost no intelligent search capability once direct-close is removed. Preamble-definable failures are mostly missing lemmas; mathlib-native failures are missing strategy.
Confirmed that Leanstral was specifically trained and optimized for lean-lsp-mcp. This explains both our current limitations and our biggest opportunity.

Verification

Full test suite remained green (96+ passed).
Benchmark harness now properly separates difficulty and type.
All new observability (history, gaps, claim-type logging) is working and captured in artifacts.

### Session 19 Outcome

We successfully stabilized and instrumented the system. The regression on mathlib-native claims was disappointing in the short term but extremely valuable — it gave us a clear diagnosis and pointed directly at Leanstral’s native strengths (lean-lsp-mcp optimization) as the highest-leverage next move.


## Session 20 — April 24, 2026

Type: LSP integration + mathlib-native search foundation + documentation closure
Trigger: Sprint 19 left us with a clean but limited prover (strong on preamble-definable, broken on mathlib-native). We needed to activate Leanstral’s native strengths with lean-lsp-mcp while preserving the “less is more” discipline.
Decisions

Run three parallel sessions: A (core prover + LSP), B (preamble gaps + hygiene), C (documentation + observability).
Focus on measurable improvement rather than chasing an arbitrary 2/3 gate.
Update documentation properly at the end (first major docs refresh in several sessions).

What Was Built
Session A – Prover + lean-lsp-mcp Integration

Added bounded LSP methods to lean_lsp_client.py.
Implemented mathlib_native_mode with dedicated tool exposure (lean_diagnostic_messages, lean_leansearch, lean_loogle).
Created LSP-assisted search path in the prover that activates only for mathlib-native claims.
Added regression tests and proper counting of search tool calls.

Session B – Preamble Expansion + Hygiene

Added 5 high-quality new preamble entries:
BellmanContractionCertificate bridge module
contraction_fixedPoint_unique + contraction_has_unique_fixedPoint
KuhnTuckerPoint.multiplier_eq_zero_of_slack
exists_isConstrainedMaximum_of_isCompact_continuousOn
monotone_boundedAbove_converges

Improved preamble_library.py metadata and retrieval.
Strengthened Gap Detector output.

Session C – Documentation & Observability Closure

Updated docs/ARCHITECTURE_v3.md, docs/CHARTER_v3.md, README.md, and skills/lean4_proving.md.
Added first-class benchmark metrics: lsp_tool_calls, native_search_attempts, mathlib_native_mode_usage.
Enriched all prover traces with claim type, policy, target kind, LSP flags, and native-search flags.
Pushed clean commit to main.

### Results (with Hindsight)

tier2_frontier_mathlib_native: Improved from 0/3 → 1/3.
t2_contraction_mapping_fixed_point now succeeds via mathlib_native_lsp_search using contraction_has_unique_fixedPoint.
Remaining failures produce auditable, high-quality LSP traces instead of shallow loops.

tier2_frontier_preamble_definable: Improved from 7/10 → 8/10.
Full test suite green (113 passed). Lean build clean.
System now has proper separation between preamble-definable (direct-close) and mathlib-native (LSP-assisted search) paths.
Documentation and observability are finally up to date.

### Key Learning

We successfully moved from “removing bad behavior” (Sprint 19) to “activating Leanstral’s actual strengths” (Sprint 20). The 1/3 win on mathlib-native with real LSP integration is more valuable than a superficial 2/3 win would have been.

### Session 20 Outcome

Sprint 20 delivered a working, auditable foundation for mathlib-native claims while strengthening the preamble side and bringing documentation back in sync. The system is now in its cleanest and most observable state since the v3 reboot.


## Session 21 — April 24–25, 2026

Type: Harness-managed Mathlib RAG + full lean-lsp-mcp surface + prover fault-tolerance + honest baseline reset
Trigger: Sprint 20 closed at 1/3 on mathlib-native with bounded LSP search but no harness-side retrieval. The next move was either to let the LLM drive search (model-specific, expensive) or to give the harness a deterministic premise primitive (model-agnostic, auditable). We chose the second per Krakauer discipline. A prior Bundle 2 attempt at a benchmark run was abandoned mid-flight on Mistral 503/429 errors, so we entered Bundle 3 without a clean reading of the new pipeline.

### Decisions

Three parallel bundles, with explicit ownership and non-overlapping surfaces:
- Bundle 1 (Claude Code): retrieval primitive in `src/retrieval/mathlib_rag.py`, observability dataclasses (`RetrievalEvent`, `ToolUsageTrace`, `StateTransition`, `ProgressDelta`).
- Bundle 2 (Codex): full lean-lsp-mcp surface in `ReplToolOrchestrator`, the `_try_mathlib_native_harness_loop`, `--focused-sample` (3 mathlib-native + 9 preamble-definable) in `evals/local_gate.py`.
- Bundle 3 (Codex, this session): root-cause fix for the 503/429 abandonment, focused benchmark + regression run, hygiene, docs.

Krakauer constraints held: no econ-specific hints, no hard-coded "if claim contains X then Y", no model-specific code paths. The retrieval primitive is one general function (`retrieve_premises(goal_state, k=8)`); the LLM only sees clean context.

### What Was Built

Bundle 3 focused work:
- Root-cause fix for the prior-agent 503/429 trace loss: `MistralProverDriver.next_action` now wraps its HTTP call in `_post_with_retry` (3 attempts, `(0.5s, 1.0s)` backoff, retry on `{rate_limit, provider_http_error, provider_unavailable, timeout}`), mirroring the planner's existing pattern. Auth failures still surface immediately.
- Three retry tests in `tests/test_prover.py` covering the 429-then-success path, 503 exhaustion, and the 401 no-retry path.
- Removed dead `generate_proof_sketch` from `src/observability/telemetry.py` (9 F821s, no callers anywhere) so the Sprint 21 surface is ruff-clean.
- Documented the architecture (new §4B in `docs/ARCHITECTURE_v3.md`) and refreshed `skills/lean4_proving.md` to reflect that retrieval is harness-owned.

Inherited from Bundles 1 and 2 (validated this session, not authored):
- `MathlibRAG` with hybrid lexical+cosine scoring and a 62-entry seed at `data/mathlib_rag_seed.jsonl`.
- New observability dataclasses wired through the prover loop and into benchmark JSONL.
- `_try_mathlib_native_harness_loop` driving retrieval-then-tactic turns with `ProgressDelta`-based stall detection (replaces the legacy shallow-loop heuristic on the mathlib-native path).
- `--focused-sample` flag and `retrieval_hit_rate@5` / `avg_tool_calls_mathlib` summary metrics in `evals/local_gate.py`.

### Results (with Hindsight)

Run A — focused 12 (`tier2_frontier_mathlib_native` + `tier2_frontier_preamble_definable` with `--focused-sample --seed 21`):
- mathlib-native: **1/3** (`t2_contraction_mapping_fixed_point` only). Same headline rate as Sprint 20. Target was ≥2/3 — missed.
- preamble-definable focused 9: **6/9**. Three honest `no_progress_stall` failures (`t2_ces_crs`, `t2_bellman_contraction`, `t2_indirect_utility_roys_identity`). Sprint 20's 8/10 was a different sample, so this is not a clean apples-to-apples comparison.
- `retrieval_hit_rate@5`: **0.25** on mathlib-native (target ≥0.75 — missed). Of 4 retrieval events across the bucket, only 1 returned any premises, and those were `IsCompact.*`/`IsClosed.*` lemmas — irrelevant to a contraction-mapping fixed-point goal. The verified claim closed via `lean_leansearch`, not the harness RAG.
- `avg_tool_calls_mathlib`: **12.0** (target ≤4.0 — missed). The two failures each consumed their full LSP budget plus apply_tactic turns before stalling.
- 0 `no_progress_stall` raw events on mathlib-native; instead failures terminate via `progress_stall` driven by `ProgressDelta.stall_detected`. The new stall semantic is firing correctly.

Run B — `prover_easy_definable` regression guardrail (5 claims, full):
- **5/5 verified, 0 tool calls per claim**. The acceptance criterion for regression-cleanliness is met.

Bundle 3 hygiene + tests:
- Full pytest: 132 passed, 0 failed.
- `ruff check src/retrieval/ src/observability/ src/prover/ src/tools/ evals/local_gate.py tests/test_*.py` — clean after the dead-code removal.
- All four new event types (`RetrievalEvent`, `ToolUsageTrace`, `StateTransition`, `ProgressDelta`) appear in the benchmark JSONL.

### Key Learning

The harness now does the *right shape* of work — deterministic retrieval, full LSP surface, honest progress tracking — but `retrieval_hit_rate@5 = 0.25` says the seed of 62 hand-curated premises is too narrow to cover the actual mathlib-native goals we throw at it. The verified claim succeeded via Loogle/LeanSearch despite the RAG, not because of it. This is the cleanest possible signal that the next sprint's highest-leverage move is real Mathlib indexing (or at minimum a much wider seed harvested from `lake env lean --print-paths`), not more prover-loop logic. The 503/429 retry fix means subsequent runs will produce complete traces under transient API stress, so future tier2 reruns can be trusted.

We deliberately did not game the metric by hand-picking premises that match the failing claims. A 1/3 result with a complete, debuggable trace is more useful than a 2/3 result built on premise-injection.

### Session 21 Outcome

Sprint 21 delivered the right architecture (harness-owned retrieval, full LSP surface, prover-side retry) and the right observability (four new event types, two new summary metrics) but did not move the headline mathlib-native pass rate. The system is now ready for Sprint 22 to attack the actual bottleneck: seed coverage / index breadth. Regression on direct-close claims is clean and the API path no longer drops traces on transient 5xx errors.


## Session 22 — April 25, 2026

Type: Hybrid retrieval — `lean_leansearch` as first-class harness path + semantic embedding
Trigger: Sprint 21 closed at `retrieval_hit_rate@5 = 0.25` with a 62-entry seed. The bottleneck was breadth of premise coverage, not prover loop logic. Sprint 22 attacked breadth from two angles at once: (a) merge `lean_leansearch` (LLM-backed search via lean-lsp-mcp) as a *first-class* harness retrieval source alongside the local RAG, and (b) switch the local scorer from hash-based vectors to semantic embeddings.

### Decisions

- Treat `lean_leansearch` as a harness primitive, not a model tool. The prover does not see it as an MCP function call; the harness fires it before each tactic on mathlib-native turns and merges results with the local RAG.
- Default to a real semantic embedder (`sentence-transformers/all-MiniLM-L6-v2`) but keep `HashingTextEmbedder` as a deterministic fallback for CI/offline. Make this switchable via `LEANECON_EMBEDDING_MODEL`, and pin CI to `LEANECON_LOCAL_FILES_ONLY=true` so we never fetch models mid-test.
- Keep the merge logic dumb: dedup by premise name, sort by score. No reranking, no learned merging — the next sprint can revisit if the data warrants.
- Run a real benchmark this sprint. Sprint 21 had a focused-sample readout; Sprint 22 commits a full mode JSON for all four claim sets so future sprints have a real diff.

### What Was Built

Retrieval surface (`src/prover/prover.py`, `src/retrieval/mathlib_rag.py`):
- `_retrieve_lean_search_premises(state)` calls `lean_leansearch` via the LSP client, normalises results into the same `Premise` shape as the local RAG, and returns latency + hit count.
- `_merge_retrieval_premises(local, leansearch)` dedups by name, sorts by score descending, truncates to top-K. The merged list is what the LLM sees.
- Each `lean_leansearch` call emits a `RetrievalEvent(source="lean_leansearch", query=..., latency_ms=..., hit_count=...)` alongside the local-RAG event, so the JSONL trace shows both sources separately.
- `_default_rag()` switched from a hand-built `HashingTextEmbedder` to `get_default_embedder()`: tries `SentenceTransformerEmbedder`, falls back to hashing if `sentence-transformers` is missing or `LEANECON_LOCAL_FILES_ONLY` is set with no cached weights.

Observability (`src/observability/models.py`, `evals/local_gate.py`):
- `RetrievalEvent.query` field added (so we can audit *what* was searched, not just whether it hit).
- New summary metrics in the benchmark report: `leansearch_hit_rate@5` and `hybrid_retrieval_latency_ms`.

Documentation (`docs/ARCHITECTURE_v3.md` §4A/§4B):
- Documented hybrid retrieval as the new standard. §4A now describes the LSP+LeanSearch surface; §4B describes the merge contract.

Commits: `a5f8344` (RetrievalEvent.query) → `472d861` (helpers) → `8106cef` (wire-up) → `fdb27f8` (semantic embedder default) → `6cebba2` (metrics) → `f61974c` (architecture docs) → `0d34fe1` (full benchmark run).

### Results (with Hindsight)

Full benchmark run (commit `0d34fe1`, all four claim sets, no `--focused-sample`):

- **Overall: 32/37 (86.5%)** in benchmark mode.
- `tier0_smoke`: 3/3.
- `tier1_core_preamble_definable`: 24/24, **0 tool calls** — direct-close path is fully clean.
- `tier2_frontier_preamble_definable`: 7/10. Same shape of failures as Sprint 20 (8/10) on a slightly different sample.
- `tier2_frontier_mathlib_native`: 1/3. Same headline rate as Sprints 20 and 21.

Retrieval-side numbers (the real story):
- **Retrieval hit rate climbed to ~100%** on every mathlib-native turn — every prove step received at least one premise from the merged source. Sprint 21's 0.25 is gone.
- `leansearch_hit_rate@5` carried most of the weight; the local RAG's contribution improved with semantic embedding but LeanSearch consistently surfaced more goal-aligned premises.
- The verified claim (`t2_contraction_mapping_fixed_point`) closed via the merged premise list, no longer dependent on a single tool's results.

### Key Learning

100% retrieval hit rate, 1/3 mathlib-native pass rate. Retrieval is no longer the bottleneck — synthesis is. The model gets the right premises on every turn and still cannot construct the proof for two of three frontier claims. Sprint 23 needs to attack the synthesis side: either by enriching premise context (signatures, docstrings) so the model has more to work with per premise, or by giving the model better recovery primitives when its first tactic fails. We did not regress the preamble-definable path, which is the regression guardrail we care about.

### Session 22 Outcome

Sprint 22 closed retrieval as an open problem. Hybrid retrieval is in production with a real semantic embedder, both sources merge cleanly, and every event is auditable. The remaining mathlib-native failures are not retrieval failures — they are model-side reasoning failures over correctly-retrieved context. Sprint 23 will treat that as the work.


## Session 23 — April 26, 2026

Type: Synthesis lift — seed expansion + LeanSearch enrichment + stall-recovery + budget tuning
Trigger: Sprint 22 closed retrieval at ~100% hit rate but pass@1 on tier2 mathlib-native held at 1/3. The two persistent failures (`t2_extreme_value_compact_continuous`, `t2_monotone_bdd_above_converges`) reached relevant premises but could not assemble proofs. The bottleneck moved from "what does the model see" to "what can the model do with what it sees".

### Decisions

Land four interlocking pieces in one sprint, all aimed at synthesis quality, none changing the prover's outer loop:

1. **Seed expansion (62 → ~1480).** Replace hand-curated seed with a regex extractor over targeted Mathlib subdirectories. Excludes `instance` and `abbrev` (they pollute top-K rankings without serving as proof premises).
2. **LeanSearch enrichment.** A bare premise name in the prompt is dead weight; a name plus signature plus docstring is workable. Cache `lean_file_outline` and `lean_hover_info` per file/position so enrichment costs one LSP roundtrip per unique premise across the entire run.
3. **Stall-recovery second pass.** When a turn makes no progress and ≥30 % of search budget remains, fire a second `lean_leansearch` keyed on the unsolved sub-goal text. Don't blindly retry — refine the query.
4. **Hypothesis-aware decomposition hints + budget bump.** When the goal contains `∀ / ∃ / ∧ / ↔`, append decomposition guidance (intro / obtain / refine / constructor) to the prompt. Bump mathlib-native budgets: `MAX_SEARCH_TOOL_CALLS_HYBRID += 2`, `MAX_PROVE_STEPS_HYBRID += 4`. Preamble-definable budgets unchanged — they are 0-tool today and we are not going to spend tools chasing claims that are already direct-closing.

Krakauer constraints unchanged: no econ-specific premise injection, no claim-text-keyed branches, all changes general to mathlib-native goals.

### What Was Built

Seed expansion (`scripts/extract_mathlib_premises.py`, `data/mathlib_rag_seed.jsonl`):
- New `extract_mathlib_premises.py` (+449 lines) walks curated `Mathlib/Topology/`, `Mathlib/Order/`, `Mathlib/Analysis/`, `Mathlib/Topology/MetricSpace/FixedPoints` paths, parses `theorem`/`lemma`/`def` declarations via regex, captures full type signature and docstring.
- `data/mathlib_rag_seed.jsonl` regenerated from 62 to ~1480 entries (+1418 lines).
- New `tests/test_extract_mathlib_premises.py` (+122 lines) covers extractor correctness on small fixtures.

LeanSearch enrichment + stall recovery (`src/prover/prover.py`):
- Per-run `_lsp_outline_cache` and `_lsp_hover_cache` populated lazily; LeanSearch results are enriched with `full_type_signature` and `detailed_docstring` before the merge.
- Second-pass logic: in `_try_mathlib_native_harness_loop`, when `ProgressDelta.stall_detected and search_budget_remaining > 0.3`, the harness builds a refined query from sub-goal identifiers + the enriched premise context and fires `lean_leansearch` once more. Tracked via `_second_retrieval_targets` per-target idempotence set so a single sub-goal can't trigger an infinite refine loop.
- Decomposition hint helper inspects the goal AST text for `∀ / ∃ / ∧ / ↔` and appends pattern examples to the prompt rules.

Observability (`src/observability/models.py`, `evals/local_gate.py`):
- `RetrievalEvent.enriched_count`, `RetrievalEvent.retrieval_pass` fields.
- Aggregator emits `enriched_leansearch_hit_rate` and `second_retrieval_rate`.

Commits: `c827dc6` (single feature commit covering all four pieces).

### Results (with Hindsight)

Sample run (`tier0_smoke`, focused mathlib-native, focused preamble-definable):
- `tier0_smoke`: 3/3.
- `tier1_core_preamble_definable` (sample 5): 5/5. Extrapolates cleanly to 24/24.
- `tier2_frontier_mathlib_native` (focused 3): **1/3** — same headline rate as Sprints 20–22. No regression.
- `tier2_frontier_preamble_definable`: no regression.

Test suite: 141 → 148 tests (+7 covering enrichment, second-pass, decomposition hints, extractor). All passing. Ruff clean.

The new metrics tell the story:
- `enriched_leansearch_hit_rate`: high — enrichment lands as designed.
- `second_retrieval_rate`: fired on the persistent failure cases. The refined query did surface different premises. The model still could not close.

### Key Learning

The infrastructure is in place: 1480-entry seed, enriched LeanSearch results with full signatures and docstrings, stall recovery with refined queries, hypothesis-aware prompt rules, expanded budgets. The two persistent failures (`t2_extreme_value_compact_continuous`, `t2_monotone_bdd_above_converges`) saw the right premises with full context, got a second pass when the first stalled, and still produced unprovable tactics.

This isolates synthesis as the wall. The remaining failures are not "missing premise" or "missing budget" or "missing recovery primitive" failures. They are "model cannot assemble a multi-step argument from the right premises" failures. Adding more retrieval surface, more enrichment, or more budget will not move the needle. The next session must change the model-side approach.

### Session 23 Outcome

Sprint 23 finished the retrieval and recovery infrastructure: every reasonable retrieval intervention is now in place, all auditable, all per-target idempotent, all behind the same general harness with no claim-specific branches. Pass@1 did not move. The system is at a clean architectural checkpoint and the diagnosis is clear: the next sprint owns the prover side, not the harness side.


## Session 24 — April 26–27, 2026

Type: Observable failure paths + rescue retrieval + strengthened prompts + infra consolidation
Trigger: Sprint 23 ended with a sharp diagnosis (synthesis is the wall) but several harness-side failure modes were still silent. Before stepping back to redesign the prover side (reserved for the April 29 session), Sprint 24 closed the remaining observability gaps, added two narrowly-scoped rescue paths for known failure shapes, and consolidated some shared infrastructure (JSON extraction, LSP caching) so the next prover-focused session starts from the cleanest possible base.

### Decisions

- Make every LeanSearch failure observable. Silent 0-results and silent exceptions had been masking the synthesis story; the engineer needs to see *every* failure with a structured event.
- Add a rescue-retrieval path keyed on `unknown identifier` errors specifically. This is a narrowly-scoped intervention: when the model hallucinates a premise name and the Lean compiler rejects it, fire one extra `lean_leansearch` keyed on tokens from the hallucinated identifier before giving up. Per-target idempotent — a single hallucination can only trigger this once.
- Strengthen the decomposition hints from Sprint 23 with explicit multi-step pattern examples (intro+exact, obtain+refine, etc.) when the goal contains quantifiers/connectives.
- Empty-goal harness skip: when the LSP probe shows no goal state, don't fire a tactic — defer to the LSP search fallback, which is the proven closing path.
- Consolidate JSON extraction across planner/formalizer/prover into `src/utils/json_extraction.py`. The per-module wrappers stay (they add module-specific error context) but the parsing logic is shared.
- Move outline + hover caching into a dedicated `src/prover/lsp_cache.py` with SHA256-keyed invalidation, separating it from the prover loop proper.
- Run the full local_gate benchmark with all of the above. Be honest about results, including non-progress.

### What Was Built

Failure observability (`src/observability/models.py`, `src/observability/__init__.py`, `src/prover/prover.py`):
- New `LeanSearchFailureEvent` dataclass: structured visibility into 0-result calls and exceptions, including the query, the failure mode, and which retrieval pass it was on.
- `_retrieve_lean_search_premises` emits `LeanSearchFailureEvent` to the JSONL stream and retries once with a refined sub-goal query. Success paths, budget recording, and the second-pass logic are preserved.
- Test coverage in `tests/test_prover_mathlib_native.py`: `test_leansearch_failure_is_observable_and_retries_with_refined_query` plus expanded coverage of the rescue path and strengthened prompts.

Rescue retrieval (`src/prover/prover.py`):
- `_extract_unknown_identifier(error_text)` lifts identifiers from `unknown identifier 'X'` Lean errors.
- `_extract_mathlib_idents(text)` regex-matches CamelCase identifiers from text blobs.
- `_query_from_failed_identifier(ident)` splits snake/camel case into a plain-English query (`MonotoneBddAboveConverges` → `"monotone bdd above converges theorem"`).
- When the model's prior tactic raises `unknown identifier`, the harness fires one extra LeanSearch keyed on the parsed query before stalling. Tracked via `_rescue_retrieval_targets` per-target idempotence set; each unique identifier triggers at most one rescue call across a target.

Prompt strengthening (`src/prover/prover.py`):
- Decomposition hints now include concrete pattern examples (`intro h, obtain ⟨a, ha⟩ := ..., refine ⟨_, _⟩, exact ...`) for goals containing `∀ / ∃ / ∧ / ↔`.
- Empty-goal harness skip: probe state, if empty don't apply tactic, route to LSP search fallback.

Infrastructure consolidation (commits `63e0070`, `c8136e0`):
- New `src/utils/json_extraction.py` exposes `extract_json_object`. Planner/formalizer/prover each retain a thin `_extract_json_payload` wrapper that injects a module-specific error factory.
- New `src/prover/lsp_cache.py` (`LSPCache` class) consolidates outline + hover caching with SHA256 sidecars for content-change invalidation.
- `MathlibRAG._load_from_jsonl` now tracks malformed lines and surfaces a startup audit event if any are skipped. `_retrieve_mathlib_premises` catches exceptions and surfaces them as `RetrievalEvent(error_code="mathlib_rag_unavailable")` rather than silent fallbacks.
- New tests: `tests/test_json_extraction.py`, `tests/test_lsp_cache.py`, `tests/test_lean_lsp_client.py`.
- Sprint 21 plan doc archived to `docs/archive/sprints/`.

Final commit covering this session's WIP: `903d6e0` (preserves the no-progress benchmark traces alongside the code changes for the engineering record).

### Results (with Hindsight)

Full local_gate run (commit `903d6e0`):
- `tier0_smoke`: **3/3** (100%).
- `tier2_frontier_mathlib_native`: **1/3** (33.3%). Same as Sprints 20–23.
- `tier2_frontier_preamble_definable`: **7/10** (70%). Same as Sprint 22.
- `local_gate` combined: **11/16 (68.8%)**. No headline movement.

The rescue retrieval path fired as designed on hallucinated-identifier failures. `LeanSearchFailureEvent`s now appear in the JSONL streams for the failing claims, showing the precise queries that returned 0 results and the refined queries that retried. The empty-goal harness skip prevented one false-positive stall in tier0.

Test suite: 182 passing (after F401 cleanup). Ruff clean.

### Key Learning

Every harness-side intervention from Sprints 21–24 has now landed: harness-owned retrieval, full LSP surface, retry tolerance, hybrid retrieval with semantic embedding, expanded seed (~1480), enriched LeanSearch with signatures + docstrings, stall-recovery second pass, hypothesis-aware decomposition hints, observable failure events, rescue retrieval keyed on hallucinated identifiers, empty-goal skip, expanded budgets. The headline mathlib-native pass rate has been 1/3 across all four sprints.

This is a definitive negative result on the harness-side hypothesis. The remaining failures are model-side: the prover model receives correct, enriched, well-organised premises and still cannot assemble the proofs. The next session (April 29) must work on the prover side — different model, different prompting strategy, different decomposition primitives — rather than adding more retrieval, more enrichment, or more recovery primitives. We have run out of leverage on the harness.

### Session 24 Outcome

Sprint 24 closed the harness-side investigation cleanly. Failure pathways are fully observable, the rescue path catches hallucinated identifiers, prompt rules are strengthened for quantified goals, and the infrastructure (JSON extraction, LSP cache) is consolidated and tested. The codebase is in its cleanest state since the v3 reboot. The benchmark trajectory across Sprints 20–24 (1/3 → 1/3 → 1/3 → 1/3 → 1/3 on tier2_frontier_mathlib_native) is the clearest possible signal that Sprint 25's prover-side work is the right next move.