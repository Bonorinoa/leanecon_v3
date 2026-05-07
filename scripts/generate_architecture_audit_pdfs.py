"""Generate LeanEcon v3 architecture white paper and agent audit PDFs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "pdf"
TMP_DIR = ROOT / "tmp" / "pdfs"


@dataclass(frozen=True)
class Evidence:
    generated_at: str
    commit: str
    recent_commit_count: int
    benchmark: dict[str, Any]
    top_files: list[tuple[int, str]]
    recent_churn: list[tuple[str, int, int]]


def _run(args: list[str]) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _line_counts() -> list[tuple[int, str]]:
    paths = [
        "src/prover/execution.py",
        "tests/test_prover.py",
        "evals/local_gate.py",
        "tests/test_planner.py",
        "tests/test_prover_mathlib_native.py",
        "src/planner/planner.py",
        "src/preamble_library.py",
        "src/formalizer/formalizer.py",
        "src/api/app.py",
        "src/api/jobs.py",
        "src/prover/retrieval.py",
        "src/prover/synthesizer.py",
        "src/retrieval/mathlib_rag.py",
    ]
    rows: list[tuple[int, str]] = []
    for rel in paths:
        path = ROOT / rel
        if path.exists():
            rows.append((len(path.read_text(encoding="utf-8").splitlines()), rel))
    return sorted(rows, reverse=True)[:10]


def _recent_churn() -> list[tuple[str, int, int]]:
    raw = _run(
        [
            "git",
            "log",
            "--since=30 days ago",
            "--numstat",
            "--format=",
            "--",
            "src",
            "evals",
            "tests",
            "docs",
        ]
    )
    totals: dict[str, tuple[int, int]] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if not added.isdigit() or not deleted.isdigit():
            continue
        prev_add, prev_del = totals.get(path, (0, 0))
        totals[path] = (prev_add + int(added), prev_del + int(deleted))
    ranked = sorted(totals.items(), key=lambda item: item[1][0] + item[1][1], reverse=True)
    return [(path, added, deleted) for path, (added, deleted) in ranked[:12]]


def collect_evidence() -> Evidence:
    benchmark = _read_json(ROOT / "benchmark_baselines" / "v3_alpha" / "benchmark_mode" / "local_gate.json")
    recent_commits = _run(
        [
            "git",
            "log",
            "--since=30 days ago",
            "--oneline",
            "--",
            "src",
            "docs",
            "evals",
            "tests",
            "lean_workspace",
        ]
    ).splitlines()
    return Evidence(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        commit=_run(["git", "rev-parse", "--short", "HEAD"]),
        recent_commit_count=len(recent_commits),
        benchmark=benchmark,
        top_files=_line_counts(),
        recent_churn=_recent_churn(),
    )


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=29,
            alignment=TA_CENTER,
            spaceAfter=14,
            textColor=colors.HexColor("#17202A"),
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#4D5656"),
            spaceAfter=22,
        ),
        "h1": ParagraphStyle(
            "Heading1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            spaceBefore=14,
            spaceAfter=8,
            textColor=colors.HexColor("#17202A"),
        ),
        "h2": ParagraphStyle(
            "Heading2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12.5,
            leading=16,
            spaceBefore=10,
            spaceAfter=5,
            textColor=colors.HexColor("#1F618D"),
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.4,
            leading=13.2,
            spaceAfter=6,
            alignment=TA_LEFT,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            spaceAfter=4,
        ),
        "callout": ParagraphStyle(
            "Callout",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12.5,
            leftIndent=8,
            rightIndent=8,
            spaceBefore=4,
            spaceAfter=4,
            textColor=colors.HexColor("#17202A"),
        ),
    }


def p(text: str, styles: dict[str, ParagraphStyle], style: str = "body") -> Paragraph:
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), styles[style])


def rawp(text: str, styles: dict[str, ParagraphStyle], style: str = "body") -> Paragraph:
    return Paragraph(text, styles[style])


def bullets(items: list[str], styles: dict[str, ParagraphStyle]) -> ListFlowable:
    return ListFlowable(
        [ListItem(p(item, styles), leftIndent=12) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=14,
        bulletFontSize=6,
    )


def table(rows: list[list[Any]], widths: list[float] | None = None) -> Table:
    converted = []
    styles = make_styles()
    for row in rows:
        converted.append([
            cell if hasattr(cell, "wrap") else Paragraph(str(cell), styles["small"]) for cell in row
        ])
    tbl = Table(converted, colWidths=widths, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF2F8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17202A")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DBDB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tbl


def callout(text: str, styles: dict[str, ParagraphStyle]) -> Table:
    tbl = Table([[p(text, styles, "callout")]], colWidths=[6.7 * inch])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8F9F9")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB7B8")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return tbl


def page_footer(canvas, doc) -> None:  # noqa: ANN001
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#566573"))
    canvas.drawString(0.72 * inch, 0.45 * inch, "LeanEcon v3 architecture audit")
    canvas.drawRightString(7.78 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def benchmark_rows(ev: Evidence) -> list[list[Any]]:
    rows = [["Claim set", "Pass@1", "Passed", "Failed", "Avg total latency"]]
    claim_sets_raw = ev.benchmark.get("claim_sets") or {}
    if isinstance(claim_sets_raw, list):
        claim_sets = {
            str(item.get("claim_set") or item.get("name") or ""): item
            for item in claim_sets_raw
            if isinstance(item, dict)
        }
    elif isinstance(claim_sets_raw, dict):
        claim_sets = claim_sets_raw
    else:
        claim_sets = {}
    for name in [
        "tier1_core_preamble_definable",
        "tier2_frontier_mathlib_native",
        "tier2_frontier_preamble_definable",
    ]:
        payload = (
            claim_sets.get(name)
            or ev.benchmark.get("bucket_breakdown", {}).get(name)
            or _read_json(ROOT / "benchmark_baselines" / "v3_alpha" / "benchmark_mode" / f"{name}.json")
            or {}
        )
        total = int(payload.get("claims_total") or 0)
        passed = int(payload.get("claims_passed") or 0)
        failed = int(payload.get("claims_failed") or 0)
        pass_rate = float(payload.get("pass_at_1") or payload.get("pass_rate") or 0.0)
        latency = float(payload.get("avg_latency_total") or payload.get("average_latency_total") or 0.0)
        if latency <= 0.0:
            timings = [
                float(((result.get("timing_breakdown") or {}).get("total_ms")) or 0.0)
                for result in payload.get("results", [])
                if isinstance(result, dict)
            ]
            timings = [item for item in timings if item > 0.0]
            latency = sum(timings) / len(timings) if timings else 0.0
        latency = latency / 1000.0
        rows.append([name, f"{pass_rate * 100:.1f}%", f"{passed}/{total}", str(failed), f"{latency:.1f}s"])
    return rows


def literature_table() -> list[list[str]]:
    return [
        ["Work", "Relevant design principle", "LeanEcon v3 implication"],
        [
            "Hilbert (ICLR 2026)",
            "Combines informal reasoning, specialized prover LLM, formal verifier, semantic retriever, and recursive decomposition.",
            "Supports the Planner/Formalizer/Prover split, but requires explicit decomposition and verifier-feedback contracts.",
        ],
        [
            "APOLLO (OpenReview/NeurIPS 2025)",
            "Uses Lean compiler feedback to repair, isolate failing sub-lemmas, invoke solvers, and reassemble verified proofs.",
            "Argues for a first-class proof repair pipeline, not ad hoc repair branches inside a long execution loop.",
        ],
        [
            "LeanDojo/ReProver (NeurIPS 2023)",
            "Treats premise selection as a bottleneck and trains retrieval over Lean proof states and accessible premises.",
            "Validates harness-owned retrieval, but suggests stronger premise accessibility, ranking, and evaluation discipline.",
        ],
        [
            "DeepSeek-Prover-V2 (2025)",
            "Uses recursive theorem proving and subgoal decomposition to mix informal and formal reasoning.",
            "Reinforces that mathlib-native gains likely need better decomposition/synthesis, not just more retrieved names.",
        ],
    ]


def build_white_paper(ev: Evidence) -> list[Any]:
    s = make_styles()
    story: list[Any] = [
        rawp("LeanEcon v3: Architecture of an Agentic Auto-Formalizer and Verifier for Economic Theory", s, "title"),
        p(f"Technical white paper. Generated {ev.generated_at} from repository commit {ev.commit}.", s, "subtitle"),
        callout(
            "Thesis: LeanEcon v3 is best understood as a thin, auditable harness around Lean 4, "
            "economic-domain preamble knowledge, claim-type routing, retrieval, and bounded proof synthesis. "
            "The Lean kernel remains the sole trust anchor; model outputs are proposals, never evidence.",
            s,
        ),
        rawp("1. Executive Summary", s, "h1"),
        p(
            "LeanEcon v3 targets research-grade formalization and verification of economic-theory claims. "
            "Its public architecture decomposes the task into an informal Planner, a Lean-aware Formalizer, "
            "a claim-type-aware Prover, and deterministic Lean verification. The most important engineering "
            "choice is not the provider model; it is the separation between untrusted model generation and "
            "kernel-checked proof artifacts.",
            s,
        ),
        p(
            "Repository evidence shows meaningful progress in benchmark discipline, observability, and "
            "claim-type routing. It also shows a clear current limit: preamble-definable claims are largely "
            "solved through pinned LeanEcon metadata and direct closure, while mathlib-native frontier claims "
            "remain difficult even after retrieval and LSP instrumentation improved. The next architecture step "
            "should therefore focus on proof-state orchestration and synthesis quality rather than merely "
            "adding more retrieval surface.",
            s,
        ),
        rawp("2. System Boundary and Trust Model", s, "h1"),
        p(
            "The API accepts natural-language economic claims and attempts to produce Lean 4 code that "
            "compiles without sorry-based unsoundness. The trust boundary is crisp: natural-language plans, "
            "formalized theorem stubs, retrieved premises, and tactic proposals are all advisory. A theorem is "
            "accepted only when Lean compilation succeeds under the configured workspace.",
            s,
        ),
        table(
            [
                ["Layer", "Responsibility", "Trust level"],
                ["Planner", "Infer assumptions, defaults, subgoals, and proof strategy.", "Untrusted model output; reviewed and schema-normalized."],
                ["Formalizer", "Translate claim and plan into Lean theorem stubs with selected preamble context.", "Untrusted until semantic/vacuity gates and Lean checks pass."],
                ["Prover", "Apply direct closure, LSP inspection, retrieval, synthesis, repair, and recursive subgoals.", "Untrusted search controller; emits trace evidence."],
                ["Lean kernel", "Compile and validate the final proof artifact.", "Authoritative verifier."],
                ["Benchmarks/observability", "Record pass rate, costs, failures, tool calls, retrieval, and synthesis events.", "Operational evidence, subject to artifact integrity."],
            ],
            [1.05 * inch, 3.75 * inch, 1.9 * inch],
        ),
        rawp("3. Literature Positioning", s, "h1"),
        p(
            "The architecture is consistent with the current direction of neural theorem proving: combine "
            "informal mathematical reasoning, retrieval, proof-assistant feedback, and compiler-checked repair. "
            "LeanEcon differs by specializing the input domain to economic theory and by maintaining an "
            "economics preamble as a domain-specific proof surface.",
            s,
        ),
        table(literature_table(), [1.35 * inch, 2.65 * inch, 2.7 * inch]),
        rawp("4. Implemented Architecture", s, "h1"),
        p(
            "The codebase implements a FastAPI surface over planner, formalizer, prover, observability, "
            "memory, benchmark, and Lean workspace modules. The intended design is 'fat skills, thin harness': "
            "domain and process knowledge live in LeanEcon preamble files, metadata, prompt skills, and benchmark "
            "artifacts; Python should remain a small orchestration layer.",
            s,
        ),
        bullets(
            [
                "Planner: provider-backed structured planning with fallback subgoals and normalized schema handling.",
                "Formalizer: provider-backed Lean stub generation, preamble context, vacuity and faithfulness guardrails.",
                "Prover: claim-type-aware proof search with direct closure for preamble-definable claims and LSP/retrieval/synthesis for mathlib-native claims.",
                "Observability: typed events for usage, audit, retrieval, LSP/search failures, state transitions, progress deltas, and synthesis events.",
                "Benchmarks: canonical split sets separate preamble-definable and mathlib-native behavior, preventing aggregate pass rates from hiding frontier weakness.",
            ],
            s,
        ),
        rawp("5. Domain Preamble and Claim-Type Policy", s, "h1"),
        p(
            "LeanEcon's domain-specific advantage is the LeanEcon Preamble: a curated Lean workspace and "
            "Python metadata layer for economic primitives, theorem templates, and tactic hints. The design is "
            "defensible when the benchmark explicitly distinguishes claims that should close against the "
            "preamble from claims that should be solved using Mathlib-native reasoning. Without that distinction, "
            "aggregate scores would overstate general proving ability.",
            s,
        ),
        table(
            [
                ["Claim type", "Permitted first search surface", "Main risk"],
                [
                    "preamble_definable",
                    "LeanEcon metadata, selected preamble names, direct closure, and compile-checked theorem stubs.",
                    "Benchmark can become a metadata lookup test rather than a formalization/proving test.",
                ],
                [
                    "mathlib_native",
                    "Lean LSP goal/diagnostic/code-action context, MathlibRAG, LeanSearch/Loogle, and bounded synthesis.",
                    "Retrieved theorem names do not guarantee valid tactic construction or proof-state progress.",
                ],
            ],
            [1.35 * inch, 3.2 * inch, 2.1 * inch],
        ),
        rawp("6. Control Flow and Evidence Flow", s, "h1"),
        p(
            "The runtime path should be read as a chain of evidence-producing transformations. The Planner "
            "produces a structured informal packet; the Formalizer turns that packet into Lean code and semantic "
            "metadata; the Prover emits trace steps, retrieval events, state transitions, progress deltas, and "
            "synthesis events; final compilation decides success. This is a stronger design than a black-box "
            "LLM prover because every failed layer can be inspected independently.",
            s,
        ),
        table(
            [
                ["Stage", "Inputs", "Outputs that should be persisted"],
                ["Plan", "Natural-language claim, context, optional theorem stub.", "Subgoals, assumptions, defaults, plan paragraph, provider usage, schema repairs."],
                ["Formalize", "Claim plus planner packet and preamble context.", "Lean theorem stub, selected preamble, semantic/vacuity results, provider usage."],
                ["Prove", "Formalization packet and claim-type policy.", "Verified code or current code, trace, targets, tool budget, retrieval/progress/synthesis events."],
                ["Aggregate", "Per-claim JSON and progress JSONL.", "Split pass rates, failure taxonomy, cost, latency, tool/LSP/search metrics."],
            ],
            [1.05 * inch, 2.45 * inch, 3.1 * inch],
        ),
        rawp("7. Current Benchmark Evidence", s, "h1"),
        p(
            "The latest checked-in benchmark-mode aggregate reports 86.5% overall pass@1, but the split "
            "surface is more informative than the headline number. Tier-1 preamble-definable claims are at "
            "24/24. Tier-2 preamble-definable claims are at 7/10. Tier-2 mathlib-native claims are at 1/3, "
            "despite high instrumentation density and hybrid retrieval.",
            s,
        ),
        table(benchmark_rows(ev), [2.55 * inch, 0.75 * inch, 0.8 * inch, 0.65 * inch, 1.05 * inch]),
        callout(
            "Interpretation: the system's strongest verified capability is domain-preamble direct closure. "
            "The frontier risk is not that LeanEcon lacks search logs; it is that mathlib-native proof synthesis "
            "does not yet reliably convert retrieved premises and LSP context into valid tactic sequences.",
            s,
        ),
        rawp("8. Engineering Rigor", s, "h1"),
        p(
            "The strongest engineering features are the benchmark split by claim type, the insistence on Lean "
            "kernel verification, and the growing typed observability model. Recent commits added retrieval and "
            "synthesis instrumentation, LSP cache boundaries, LeanSearch failure events, rescue retrieval, and "
            "metrics for synthesis efficiency and premise match rate.",
            s,
        ),
        table(
            [
                ["Evidence type", "Current implementation signal", "Reviewer interpretation"],
                ["Kernel verification", "Final proof code must compile through Lean; sorry is treated as failure.", "Correct trust anchor."],
                ["Traceability", "RetrievalEvent, ToolUsageTrace, StateTransition, ProgressDelta, and SynthesisEvent are surfaced in benchmark artifacts.", "Good auditability, especially for failed mathlib-native runs."],
                ["Cost/latency", "Benchmark summaries record per-stage usage and average latency.", "Operationally useful but not a proof-quality metric."],
                ["Failure taxonomy", "compile_failed, lsp_unavailable, max_turns_exhausted, schema_invalid and related codes are aggregated.", "Good starting point; needs finer mathlib-native synthesis classes."],
            ],
            [1.35 * inch, 2.9 * inch, 2.4 * inch],
        ),
        p(
            "The main structural concern is that proof execution remains too broad. The 30-day history shows "
            "52 commits touching core implementation and documentation, with repeated changes in prover control "
            "flow. The late split left `src/prover/prover.py` as a compatibility facade, but `src/prover/execution.py` "
            "now carries the proof loop, target iteration, shortcut policy, final compilation, event emission, and "
            "failure normalization. This is understandable after a rescue sprint, but it is not the right long-term "
            "unit of change.",
            s,
        ),
        table(
            [["Largest files in active surface", "Lines"]]
            + [[path, str(lines)] for lines, path in ev.top_files[:8]],
            [4.9 * inch, 0.8 * inch],
        ),
        rawp("9. API and Benchmark Parity", s, "h1"),
        p(
            "The benchmark harness currently appears more mature than the public API for long-running "
            "end-to-end proof runs. The benchmark path records detailed progress and aggregate artifacts; the "
            "API exposes jobs, review transitions, health, metrics, and SSE, but the reportable nuance during "
            "active proof search is thinner. For a research API, this matters: users need to know whether a "
            "failure was caused by formalization, LSP outage, premise retrieval, tactic synthesis, or final "
            "compilation.",
            s,
        ),
        callout(
            "Release criterion: a production `/prove` job should expose the same semantic progress categories "
            "that local_gate records: active target, claim type, LSP/search activity, retrieved premise names, "
            "candidate tactic outcomes, progress deltas, and terminal failure code.",
            s,
        ),
        rawp("10. Limitations and Threats to Validity", s, "h1"),
        bullets(
            [
                "The canonical benchmark is still small for mathlib-native claims: 3 frontier claims are enough to reveal a bottleneck but not enough to estimate broad capability.",
                "Preamble-definable success depends heavily on theorem stubs, metadata, and direct closure; this is useful engineering, but it should not be conflated with general theorem proving.",
                "The benchmark harness has richer progress artifacts than the public API stream, so API-readiness claims should remain conservative until long-running jobs stream meaningful intermediate state.",
                "Retrieved-premise hit rates do not imply proof success; the measurable gap is now premise use and tactic synthesis.",
                "Provider behavior and external LSP/search availability are operational dependencies, so release claims need pinned model configuration and captured environment metadata.",
            ],
            s,
        ),
        rawp("11. Recommended Path Forward", s, "h1"),
        p(
            "The next phase should treat the prover as a state-machine and policy-composition problem. Instead "
            "of adding more local heuristics to the execution loop, define explicit contracts for proof state, "
            "retrieval inputs/outputs, synthesis candidates, Lean file mutations, repair attempts, and terminal "
            "failure classifications.",
            s,
        ),
        table(
            [
                ["Priority", "Recommendation", "Reason"],
                ["P0", "Extract a proof-state orchestrator with explicit states and transitions.", "Reduces regression risk in the current high-churn execution loop."],
                ["P0", "Separate Lean file mutation from proof policy.", "String surgery on theorem bodies should be isolated and exhaustively tested."],
                ["P1", "Make retrieval and synthesis pluggable policies with shared typed records.", "Allows experiments without changing core execution semantics."],
                ["P1", "Align public API SSE with benchmark progress events.", "Makes production behavior auditable, not only local benchmark behavior."],
                ["P2", "Grow mathlib-native claim set before broad release claims.", "Prevents aggregate pass rates from masking frontier brittleness."],
            ],
            [0.55 * inch, 3.0 * inch, 2.3 * inch],
        ),
        rawp("12. Research Claims That Are Currently Defensible", s, "h1"),
        bullets(
            [
                "LeanEcon v3 is an auditable Lean 4 harness for economic-theory formalization experiments.",
                "The system has a strong preamble-definable path and a clearly instrumented but still weak mathlib-native frontier path.",
                "The architecture aligns with modern theorem-proving systems that combine informal reasoning, retrieval, formal verifier feedback, and recursive decomposition.",
                "The current evidence supports a claim of engineering progress and measurable bottleneck isolation, not yet broad autonomous theorem-proving competence.",
            ],
            s,
        ),
        rawp("References", s, "h1"),
        bullets(
            [
                "Hilbert: Recursively Building Formal Proofs with Informal Reasoning. ICLR 2026 poster. https://iclr.cc/virtual/2026/poster/10010497",
                "APOLLO: Automated LLM and Lean Collaboration for Advanced Formal Reasoning. OpenReview/NeurIPS 2025. https://openreview.net/forum?id=fxDCgOruk0",
                "LeanDojo: Theorem Proving with Retrieval-Augmented Language Models. NeurIPS 2023. https://papers.nips.cc/paper_files/paper/2023/hash/4441469427094f8873d0fecb0c4e1cee-Abstract-Datasets_and_Benchmarks.html",
                "DeepSeek-Prover-V2: Advancing Formal Mathematical Reasoning via Reinforcement Learning for Subgoal Decomposition. arXiv 2025. https://arxiv.org/abs/2504.21801",
            ],
            s,
        ),
    ]
    return story


def build_agent_audit(ev: Evidence) -> list[Any]:
    s = make_styles()
    story: list[Any] = [
        rawp("LeanEcon v3 Agent Audit", s, "title"),
        p(f"Companion implementation audit. Generated {ev.generated_at} from repository commit {ev.commit}.", s, "subtitle"),
        callout(
            "Purpose: give future LLMs and coding agents a decision-complete map for improving the codebase "
            "without rediscovering the same architecture debt. This document is intentionally more direct than "
            "the white paper.",
            s,
        ),
        rawp("1. Current State", s, "h1"),
        p(
            "LeanEcon v3 has moved beyond a prototype: it has a coherent module layout, typed API models, "
            "benchmark artifacts, a Lean workspace, and meaningful tests. The stagnation is concentrated in "
            "mathlib-native proving, where multiple sprints improved retrieval and observability but did not "
            "substantially raise pass@1. The codebase should now optimize for architectural leverage, not more "
            "local proof-loop patches.",
            s,
        ),
        table(
            [
                ["Area", "Observed condition", "Agent guidance"],
                ["API", "FastAPI jobs, review gates, health/metrics, SQLite job store.", "Preserve contracts; improve long-running progress and event-loop isolation."],
                ["Planner", "Structured provider drivers plus fallback repair.", "Avoid planner churn unless schema or packet handoff is failing."],
                ["Formalizer", "Lean stub generation with preamble context and guardrails.", "Keep semantic/vacuity checks before prover input."],
                ["Prover", "Largest and highest-churn subsystem; split into facade plus mixins.", "Refactor around explicit proof states before adding heuristics."],
                ["Benchmarks", "Canonical split by claim type; aggregate runner and history.", "Treat split metrics as release gates; do not optimize only overall pass rate."],
            ],
            [1.05 * inch, 2.95 * inch, 2.65 * inch],
        ),
        rawp("2. High-Risk Modules", s, "h1"),
        p(
            "The main risk is not a lack of modules; it is that the most volatile behavior still crosses module "
            "boundaries implicitly. Agents should avoid changing public prover behavior through incidental edits "
            "to helper functions unless they also update focused tests and benchmark observability expectations.",
            s,
        ),
        table(
            [["File", "Lines", "Risk note"]]
            + [
                [path, str(lines), _risk_note(path)]
                for lines, path in ev.top_files[:9]
            ],
            [2.5 * inch, 0.55 * inch, 3.0 * inch],
        ),
        rawp("3. Churn Evidence", s, "h1"),
        p(
            f"Recent git history shows {ev.recent_commit_count} commits in the last 30 days touching active "
            "code, docs, evals, tests, or Lean workspace files. The largest churn items are concentrated around "
            "the prover and benchmark harness.",
            s,
        ),
        table(
            [["Path", "Added", "Deleted"]]
            + [[path, str(added), str(deleted)] for path, added, deleted in ev.recent_churn[:10]],
            [4.25 * inch, 0.65 * inch, 0.65 * inch],
        ),
        rawp("4. Architectural Findings", s, "h1"),
        bullets(
            [
                "Good: claim-type routing is the right abstraction. It prevents preamble shortcuts from polluting mathlib-native evaluation.",
                "Good: benchmark artifacts now expose retrieval, LSP, progress, and synthesis events, making failures inspectable.",
                "Good: `src/prover/prover.py` preserves historical imports while the implementation was split into mixins.",
                "Concern: `src/prover/execution.py` is still a god-loop. It coordinates target iteration, shortcuts, helper theorem injection, final compilation, telemetry, failure mapping, and persistence side effects.",
                "Concern: Lean code mutation is still partly string-oriented and close to execution policy. This raises regression risk when adding theorem-shape repairs.",
                "Concern: the public API stream is less expressive than benchmark progress logs, so production runs are harder to debug than local gate runs.",
                "Concern: mathlib-native pass rate is too sensitive to three focused claims; expand the claim set before claiming broad frontier ability.",
            ],
            s,
        ),
        rawp("5. Proposed Internal Contracts", s, "h1"),
        p(
            "The following contracts are the recommended target architecture for the next implementation pass. "
            "Names can vary, but the boundaries should not: execution should coordinate typed policies instead "
            "of owning retrieval, synthesis, Lean mutation, and failure normalization directly.",
            s,
        ),
        table(
            [
                ["Contract", "Owns", "Must not own"],
                ["ProofSessionState", "Current code, target, goals, diagnostics, state hash, previous transition.", "Provider calls or file rewriting policy."],
                ["LeanPatchPlanner", "Proof-site resolution, theorem-body replacement, helper theorem insertion.", "Retrieval, tactic ranking, or benchmark metrics."],
                ["RetrievalPolicy", "Goal-conditioned MathlibRAG/LeanSearch/Loogle calls, merge, enrichment, budget accounting.", "Applying tactics or mutating Lean code."],
                ["SynthesisPolicy", "Proof sketch, tactic candidates, premise-use detection, helper-lemma request.", "Lean compilation or API persistence."],
                ["ExecutionOrchestrator", "State transitions, loop termination, policy invocation, final verification.", "Provider-specific prompting details."],
            ],
            [1.45 * inch, 2.75 * inch, 2.45 * inch],
        ),
        rawp("6. Refactor Roadmap", s, "h1"),
        table(
            [
                ["Step", "Implementation directive", "Acceptance test"],
                [
                    "1",
                    "Introduce a typed `ProofSessionState` record that owns current code hash, active target, goals, diagnostics, retrieved premises, and last transition.",
                    "Existing prover tests pass; new unit tests can construct state without launching provider drivers.",
                ],
                [
                    "2",
                    "Move theorem-body replacement, helper injection, and sorry-site resolution behind a `LeanPatchPlanner` or equivalent file-mutation boundary.",
                    "Golden tests cover theorem body, named have, helper lemma, multiple-sorry, and no-proof-site cases.",
                ],
                [
                    "3",
                    "Define retrieval policy interface: input proof state -> typed retrieval bundle. Keep MathlibRAG and LeanSearch merge behind that policy.",
                    "Retrieval tests assert budget behavior, failure events, dedupe, enrichment, and retry semantics.",
                ],
                [
                    "4",
                    "Define synthesis policy interface: proof state + retrieval bundle -> ranked tactic candidates and optional helper-lemma request.",
                    "Mathlib-native tests assert `SynthesisEvent`, premise-match metrics, and deterministic candidate ordering.",
                ],
                [
                    "5",
                    "Have execution loop consume only state transitions and policy outputs; remove direct retrieval/synthesis/file-surgery decisions from the loop.",
                    "Trace payloads remain backward-compatible and local_gate aggregate output is unchanged except for intentional new fields.",
                ],
                [
                    "6",
                    "Bridge benchmark progress events into API SSE for active proof jobs.",
                    "API smoke test observes non-terminal prover progress before final job state.",
                ],
            ],
            [0.45 * inch, 4.2 * inch, 2.0 * inch],
        ),
        rawp("7. Failure Modes to Preserve", s, "h1"),
        table(
            [
                ["Failure class", "Meaning", "Implementation requirement"],
                ["schema_invalid", "Planner or formalizer provider response failed expected schema.", "Persist raw response hash or sanitized raw response for debugging."],
                ["compile_failed", "Final Lean code did not compile cleanly.", "Include Lean diagnostics and final code snapshot where safe."],
                ["lsp_unavailable", "LSP/LeanSearch tooling unavailable or failed.", "Do not silently consume; emit structured audit event and continue only if fallback is defined."],
                ["max_turns_exhausted", "Search loop ran out of steps without proof.", "Include last state hash, active goals, and progress delta."],
                ["no_progress_stall", "Tactic/action did not reduce goals or complexity.", "Record candidate tactic, referenced premises, and decomposition depth."],
            ],
            [1.25 * inch, 2.35 * inch, 3.0 * inch],
        ),
        rawp("8. Test Matrix", s, "h1"),
        table(
            [
                ["Change area", "Minimum tests"],
                ["Proof execution loop", "`tests/test_prover.py` focused target, final compile, failure normalization, and trace-context tests."],
                ["Mathlib-native retrieval/synthesis", "`tests/test_prover_mathlib_native.py`, `tests/test_mathlib_rag.py`, and synthesis metric tests."],
                ["Benchmark artifact shape", "`tests/test_local_gate.py`, `tests/test_aggregate_benchmarks.py`, and `tests/test_metrics_aggregator.py`."],
                ["API job lifecycle/SSE", "`tests/test_api_smoke.py` plus a non-terminal event assertion for active prove jobs."],
                ["Lean preamble or metadata", "Lean workspace build or focused Lean compile, plus `tests/test_preamble_library.py`."],
            ],
            [1.65 * inch, 4.85 * inch],
        ),
        rawp("9. Implementation Rules for Future Agents", s, "h1"),
        bullets(
            [
                "Do not optimize aggregate pass@1 without reporting split metrics for preamble-definable and mathlib-native claims.",
                "Do not weaken Lean verification, sorry detection, claim-type policies, or benchmark observability to gain pass rate.",
                "Do not add provider-specific assumptions inside execution policy; keep model behavior behind drivers and prompt/synthesis policies.",
                "When touching `src/prover/execution.py`, add or update a focused test in `tests/test_prover.py` or `tests/test_prover_mathlib_native.py`.",
                "When changing benchmark output shape, update `evals/local_gate.py`, `src/evals/metrics_aggregator.py`, and aggregate benchmark tests together.",
                "When changing Lean preamble metadata, verify the Lean workspace and keep Python metadata consistent with Lean source truth.",
            ],
            s,
        ),
        rawp("10. Stop-Doing List", s, "h1"),
        bullets(
            [
                "Stop adding one-off theorem-shape fixes directly to the execution loop unless they are emergency patches with follow-up extraction.",
                "Stop treating retrieval hit rate as sufficient evidence of prover improvement.",
                "Stop adding benchmark claims without classifying whether they test preamble definitions, planner/formalizer faithfulness, or mathlib-native proof search.",
                "Stop relying on terminal-only benchmark progress when API users need the same observability through job events.",
                "Stop expanding provider combinations until the default stack has stable split benchmarks and failure taxonomy.",
            ],
            s,
        ),
        rawp("11. Next Sprint Definition of Done", s, "h1"),
        table(
            [
                ["Deliverable", "Definition of done"],
                ["Proof-state state machine", "At least one mathlib-native path runs through explicit state records, with old trace shape preserved."],
                ["File mutation boundary", "All theorem-body and helper insertion cases are covered by direct unit tests."],
                ["Synthesis diagnostics", "Each failed mathlib-native claim reports whether tactics referenced top retrieved premises and why the final transition stalled."],
                ["API progress parity", "A `/prove` job emits meaningful intermediate SSE progress, not only terminal updates."],
                ["Expanded frontier sample", "Mathlib-native claim count increases beyond 3 with documented categories and expected proof ingredients."],
            ],
            [2.0 * inch, 4.25 * inch],
        ),
        rawp("12. Agent Handoff Prompt", s, "h1"),
        callout(
            "You are working in LeanEcon v3. Preserve Lean kernel verification and claim-type split metrics. "
            "Your next task is to reduce prover architecture risk by extracting proof-state orchestration and "
            "Lean file mutation from `src/prover/execution.py`. Do not change benchmark semantics unless tests "
            "and aggregate metrics are updated. Validate with focused prover, mathlib-native, local-gate, and "
            "metrics tests before proposing broader benchmark runs.",
            s,
        ),
    ]
    return story


def _risk_note(path: str) -> str:
    if path == "src/prover/execution.py":
        return "Primary refactor target: broad execution loop with policy, mutation, telemetry, and failure side effects."
    if "test_prover" in path:
        return "Valuable regression suite; likely brittle because it tracks historical monkeypatch surfaces."
    if path == "evals/local_gate.py":
        return "Benchmark harness is central operational truth; keep output compatibility."
    if path.startswith("src/api"):
        return "Public surface area; changes require API smoke and SSE/job lifecycle tests."
    if path.startswith("src/planner"):
        return "Provider/schema repair logic; avoid churn unless packet handoff changes."
    return "Active supporting surface; update targeted tests when behavior changes."


def build_pdf(path: Path, story: list[Any]) -> None:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.7 * inch,
        title=path.stem,
        author="LeanEcon v3 audit",
    )
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    ev = collect_evidence()
    build_pdf(OUT_DIR / "leanecon_v3_technical_white_paper.pdf", build_white_paper(ev))
    build_pdf(OUT_DIR / "leanecon_v3_agent_audit.pdf", build_agent_audit(ev))


if __name__ == "__main__":
    main()
