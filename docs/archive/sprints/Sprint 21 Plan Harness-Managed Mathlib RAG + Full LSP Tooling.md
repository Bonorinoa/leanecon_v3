# Sprint 21 Plan: Harness-Managed Mathlib RAG + Full LSP Tooling  
**Lean Econ v3 — Model-Agnostic Foundation Sprint**  
  
**Date**: April 24, 2026  
  
**Theme**: "Less is more" (Krakauer) — Observability-first, harness-owned retrieval, zero model-specific hacks.  
  
**Goal**: Deliver ≥2/3 pass rate on focused mathlib-native + hybrid frontier sample via clean harness RAG + full lean-lsp-mcp surface. Close with live ==local-gate --benchmark-mode== validation.  
⸻  
## 1. Strategic Rationale (Why This Sprint Is Correct)  
  
### Does This Contradict "Unleashing Leanstral"?  
**No — it is the correct way to unleash it.**  
  
Leanstral was specifically trained (March 2026) to excel in *realistic formal repository workflows* using **lean-lsp-mcp** as its primary interface (diagnostics, goals, hover, code actions, external search). The model expects:  
- Rich, structured context (current goal state + relevant premises + full diagnostics)  
- Tight verifier-in-the-loop feedback  
- Ability to propose tactics while the environment handles retrieval and progress tracking  
  
The previous 1/3 result on ==tier2_frontier_mathlib_native== came from under-using exactly this interface. The Sprint 21 design gives Leanstral *precisely* the environment it was trained for — while keeping every retrieval, progress, and orchestration decision in the harness.  
  
**Trade-offs: Harness-Managed RAG vs LLM-Managed Search**  

| Dimension | Harness-Managed RAG (This Plan) | LLM-Managed Search (Alternative) | Winner for Long-Term |
| ------------------------- | ------------------------------------------------------- | --------------------------------------------------- | -------------------- |
| Model Agnosticism | Full — any LLM works the same way | Partial — favors models good at tool-use | Harness |
| Cost per Claim | Lower (one semantic retrieval + few LLM turns) | Higher (LLM may make many search calls) | Harness |
| Consistency | High — deterministic retrieval + traces | Variable — depends on model mood | Harness |
| Debuggability | Excellent — full RetrievalEvent + ToolUsageTrace | Poor — hidden inside LLM reasoning | Harness |
| Future-Proofing | Excellent — swap Leanstral for Grok-4 / Claude-4 / etc. | Risky — next model may be worse at search | Harness |
| Short-term Leanstral Perf | Equal or better (gives it perfect context) | Marginally higher on some claims (but inconsistent) | Tie / Harness |
| Engineering Complexity | Medium (one clean retrieval primitive) | Low (let LLM do it) | Harness (long-term) |
  
  
**Outside Options Considered**  
- **LeanCopilot native in-process inference** — Powerful (zero latency, direct ==InfoView== access) but adds heavy Lean metaprogramming dependency and reduces model-agnostic surface.   
- **Full ReProver-style fine-tuned prover head** — Excellent results in research, but requires training infrastructure we do not yet have. Sprint 23 will collect the exact traces needed for this.  
- **Keep LLM doing its own retrieval** — Rejected. Violates "less is more", increases cost, and contradicts the research consensus (LeanDojo, ReProver, LeanCopilot all use external retrieval + LLM-as-proposer).  
  
**Long-Term Vision Alignment**   
The moat is **not** any single model. The moat is:  
1. The Preamble Library (kernel-validated, growing)  
2. The thin, observable harness (this sprint)  
3. The rich trace corpus we will accumulate (future fine-tuning / distillation)  
  
This sprint is the correct engineering foundation.  
⸻  
## 2. Sprint 21 Scope & Constraints  
  
**In Scope**  
- Harness-owned Mathlib RAG (semantic + optional BM25) in ==src/context/== or ==src/retrieval/==  
- Full lean-lsp-mcp tool surface in ==ReplToolOrchestrator== (mathlib_native path only)  
- Progress detection via state hash + goal delta (no custom tactics)  
- Comprehensive observability (==RetrievalEvent==, ==ToolUsageTrace==, ==StateTransition==, ==ProgressDelta==)  
- Focused benchmark sample run + hygiene + docs  
  
**Out of Scope (Explicitly Deferred)**  
- Any econ-specific hints, specialized tactics, or claim-type hard-coding  
- Changes to preamble_definable path (must remain 0-tool where possible)  
- New model backends or planner changes  
- Full tier1 + tier2 re-run (only focused 10–12 claim sample)  
  
**Krakauer Constraints**  
- Zero hard-coded hints or "if claim contains X then try Y"  
- Retrieval is a single, general primitive (==retrieve_premises(goal_state, k=8)==)  
- LLM receives only clean context; harness owns all search and progress logic  
⸻  
## 3. Task Bundles & Parallel Execution  
  
### Bundle 1 — Claude Code (Architecture + Observability Spine)  
**Owner**: Claude Code  
  
**Duration**: Days 1–4 (parallel with Bundle 2)  
  
**Tasks**  
1. Design & implement ==src/retrieval/mathlib_rag.py== (or extend ==ContextManager==)  
    - Lightweight persistent index: name, type, docstring, dependencies, file path  
    - ==retrieve_premises(goal_state: str, k: int = 8) -> List[Premise]== with relevance scores  
    - Index stored at ==lean_workspace/.cache/mathlib_rag.jsonl== (rebuilt on ==lake build== if stale)  
2. Add observability models in ==src/observability/models.py==:  
    - ==RetrievalEvent(retrieved_premises, scores, latency_ms)==  
    - ==ToolUsageTrace(tool_name, args, result, state_hash_before, state_hash_after)==  
    - ==StateTransition(goal_count_before, goal_count_after, progress_delta)==  
    - ==ProgressDelta(goals_reduced, complexity_reduced, stall_detected)==  
3. Wire traces into existing ==Telemetry== and benchmark JSONL emitter  
4. Refactor ==src/prover/prover.py== to remove any remaining Leanstral-specific assumptions  
  
**Acceptance Criteria**  
- ==retrieve_premises== returns ranked premises with scores ≥0.75 on known-good test goals  
- All new events appear in benchmark JSONL with correct schema  
- ==ruff check== + ==pytest tests/test_observability.py== pass  
  
### Bundle 2 — Codex (Execution + Prover Integration + Benchmark Runner)  
**Owner**: Codex  
  
**Duration**: Days 1–7 (parallel with Bundle 1)  
  
**Tasks**  
1. Expand ==src/prover/repl.py== ==ReplToolOrchestrator== (mathlib_native branch) with full lean-lsp-mcp surface:  
    - Priority tools: ==lean_goal==, ==lean_diagnostic_messages==, ==lean_local_search==, ==code_actions== (==simp?==/==exact?==/==apply?==), ==lean_file_outline==  
    - New harness-driven loop:  
    - while not solved and not stall:  
    -     state = get_current_state()          # hash + goals + diagnostics  
    -     premises = harness.retrieve_premises(state.goals)  
    -     context = build_prompt(state, premises, diagnostics)  
    -     action = llm.propose(context)        # Leanstral or any provider  
    -     result = apply_tactic(action)  
    -     record ToolUsageTrace + ProgressDelta  
    -     if state_hash_unchanged and goals_unchanged:  
    -         stall = True  
  
2. Update ==evals/local_gate.py==:  
    - Add ==--focused-sample== (hardcoded 12 claim IDs: 3 mathlib_native + 9 hybrid frontier)  
    - Ensure ==--benchmark-mode== emits all new trace types  
    - New metrics: ==retrieval_hit_rate@5==, ==avg_tool_calls_mathlib==, ==progress_deltas==  
3. Add tests in ==tests/test_prover.py== for new loop and traces  
  
**Acceptance Criteria**  
- mathlib-native claims use ≥1 retrieved premise on ≥80% of turns  
- 0 ==no_progress_stall== (replaced by observable ==ProgressDelta.stall_detected==)  
- ==pytest tests/test_prover.py -q== passes  
  
### Bundle 3 — Codex (Evaluation + Hygiene + Docs)  
**Owner**: Codex  
  
**Duration**: Days 6–8 (after Bundles 1+2 stabilize)  
  
**Tasks**  
1. Run final focused benchmark: ==python -m evals.local_gate --claim-sets tier2_frontier_mathlib_native,prover_easy_definable --benchmark-mode --seed 21 --focused-sample==  
2. Hygiene sweep: ==ruff check --fix==, dead code removal, coverage check  
3. Documentation:  
    - Update ==docs/ARCHITECTURE_v3.md== (new section: "Harness RAG & Model-Agnostic Mathlib Interaction")  
    - Add Session 21 entry to ==LeanEcon Engineering Log.md==  
    - Minor update to ==skills/lean4_proving.md== if tool patterns changed  
  
**Acceptance Criteria**  
- Benchmark JSONL contains full traces for all 12 claims  
- mathlib-native pass rate ≥2/3 on the sample  
- No regression on ==prover_easy_definable== (5/5, 0 tools)  
- Clean git diff with conventional commit message  
⸻  
## 4. Exact Commands to Run at Each Stage  
  
### Stage 0 — Pre-Sprint Sanity (Run Before Any Code Change)  
```
cd /home/workdir
python -m pytest tests/test_prover.py tests/test_local_gate.py -q --tb=no
python -m evals.local_gate --claim-sets tier2_frontier_mathlib_native,prover_easy_definable --benchmark-mode --seed 17 --max-claims 5

```
  
  
### Stage 1 — After Bundle 1 (RAG + Observability)  
```
cd /home/workdir
python -c "
from src.retrieval.mathlib_rag import MathlibRAG
rag = MathlibRAG()
premises = rag.retrieve_premises('theorem h : Continuous f → Continuous (λ x, f x + g x)', k=5)
print([p.name for p in premises])
print('RAG OK' if len(premises) >= 3 else 'RAG FAIL')
"
python -m pytest tests/test_observability.py -q --tb=short

```
  
  
### Stage 2 — After Bundle 2 (Prover Loop Integration)  
```
cd /home/workdir
python -m pytest tests/test_prover.py -q --tb=no -k "mathlib or lsp or retrieval"
python -c "
from src.prover.repl import ReplToolOrchestrator
# smoke test that new tools are registered
print('Tools registered:', orchestrator.list_tools())
"

```
  
  
### Stage 3 — Final Benchmark Run (Bundle 3)  
```
cd /home/workdir
python -m evals.local_gate \
  --claim-sets tier2_frontier_mathlib_native,prover_easy_definable \
  --benchmark-mode \
  --seed 21 \
  --focused-sample \
  --max-claims 12

```
  
  
**Expected Output (Success Criteria)**  
- ==mathlib_native== bucket: ≥2/3 passed  
- ==retrieval_hit_rate@5==: ≥0.75  
- ==avg_tool_calls_mathlib==: ≤4.0 (down from previous ~6+)  
- 0 ==no_progress_stall== failures  
- Full ==RetrievalEvent== and ==ProgressDelta== traces present in JSONL  
  
### Stage 4 — Hygiene & Commit  
```
cd /home/workdir
ruff check --fix src/ evals/ tests/
ruff format src/ evals/ tests/
python -m pytest tests/ -q --tb=no
git status
git add -A
git commit -m "feat(prover): harness-managed Mathlib RAG + full lean-lsp-mcp (obs-first, model-agnostic) — Sprint 21

- Harness owns retrieval and progress tracking
- Full LSP surface (goal, diagnostics, code_actions, local_search)
- Comprehensive traces: RetrievalEvent, ToolUsageTrace, ProgressDelta
- Focused 12-claim benchmark: mathlib_native ≥2/3, 0 stall
- No model-specific hacks; preamble_definable path untouched"

```
  
⸻  
****5. Success Metrics & Definition of Done****  

| Metric | Target (Sprint 21) | How Measured |
| ----------------------------- | ------------------------ | ------------------------------------ |
| mathlib-native pass rate | ≥2/3 (≥2/3 claims) | local-gate JSONL |
| Retrieval hit rate @5 | ≥0.75 | RetrievalEvent count in traces |
| Avg tool calls (mathlib) | ≤4.0 | ToolUsageTrace aggregation |
| no_progress_stall count | 0 | ProgressDelta.stall_detected |
| preamble_definable regression | 5/5, 0 tools | Same as before |
| Observability coverage | 100% of new paths traced | Schema validation in benchmark JSONL |
| Test coverage | No drop | pytest --cov |
  
  
**Definition of Done**  
- All three bundles merged  
- Final benchmark run passes success metrics  
- Hygiene sweep complete  
- ==docs/ARCHITECTURE_v3.md== + Engineering Log updated  
- Clean push to ==main==  
⸻  
## 6. Forward Look (Sessions 22–25)  
  
- **22**: Progress-guided best-first search (harness level, using ==ProgressDelta==)  
- **23**: LeanDojo-style repository tracing on LeanEcon + Mathlib → lifelong episodic memory  
- **24**: Full tier1_core + tier2_frontier re-run. First publishable public benchmarks reflecting the honest capabilities of our system.   
- **25**: Design Railway (Hobby plan) deployment strategy from main and prep codebase for alpha deploy (only if sustained ≥98% on local tests)  
  
**This sprint is the correct, non-contradictory foundation for unleashing Leanstral while building a model-agnostic future.**  
⸻  
*End of Sprint 21 Plan*  
