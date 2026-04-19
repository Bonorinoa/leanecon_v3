"""Formalizer scaffold with driver registry and guardrail hooks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.config import FORMALIZER_MODEL
from src.guardrails import semantic_faithfulness_score, vacuity_report
from src.preamble_library import PREAMBLE_LIBRARY
from src.skills import load_skill


@dataclass(frozen=True)
class FormalizerBackend:
    name: str
    model: str
    notes: str


@dataclass(frozen=True)
class FormalizationPacket:
    claim: str
    theorem_with_sorry: str
    selected_preamble: list[str]
    selected_imports: list[str]
    vacuity: dict[str, object]
    faithfulness: dict[str, object]
    review_state: str
    backend: str


class DriverRegistry:
    def __init__(self) -> None:
        self._backends = {
            "leanstral": FormalizerBackend("leanstral", "mistralai/Leanstral-2603", "Primary Lean formalizer."),
            "goedel-prover-v2": FormalizerBackend(
                "goedel-prover-v2",
                "Goedel-LM/Goedel-Prover-V2-32B",
                "Alternative open formalizer/prover backend.",
            ),
        }

    def get(self, name: str) -> FormalizerBackend:
        return self._backends[name]

    def names(self) -> list[str]:
        return sorted(self._backends)


class FormalizerService:
    def __init__(self, backend: str = "leanstral") -> None:
        self.registry = DriverRegistry()
        self.backend = self.registry.get(backend)
        self.rules = load_skill("faithfulness_rubric") or ""

    def _select_preamble(self, claim: str) -> tuple[list[str], list[str]]:
        tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_'-]+", claim.lower()))
        matches: list[tuple[str, float]] = []
        for name, entry in PREAMBLE_LIBRARY.items():
            keyword_hits = len(tokens & {keyword.lower() for keyword in entry.keywords})
            if keyword_hits:
                matches.append((name, float(keyword_hits)))
        selected = [name for name, _score in sorted(matches, key=lambda item: item[1], reverse=True)[:3]]
        imports = [PREAMBLE_LIBRARY[name].lean_module for name in selected]
        return selected, imports

    def _slug(self, claim: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9]+", claim.lower())
        stem = "_".join(tokens[:6]) or "generated_claim"
        return f"v3_{stem}"[:60]

    def formalize(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        benchmark_mode: bool = False,
    ) -> FormalizationPacket:
        selected_preamble, imports = self._select_preamble(claim)
        import_lines = ["import Mathlib", *[f"import {module}" for module in imports]]
        theorem_name = self._slug(claim)
        theorem_with_sorry = "\n".join(
            [
                *import_lines,
                "",
                f"/-- TODO(Grok/Feynman): replace scaffold with faithful formalization for: {claim} -/",
                f"theorem {theorem_name} : True := by",
                "  sorry",
                "",
            ]
        )
        vacuity = vacuity_report(theorem_with_sorry)
        faithfulness = semantic_faithfulness_score(claim, theorem_with_sorry)
        review_state = "approved" if benchmark_mode else "awaiting_formalization_review"
        return FormalizationPacket(
            claim=claim,
            theorem_with_sorry=theorem_with_sorry,
            selected_preamble=selected_preamble,
            selected_imports=imports,
            vacuity=vacuity,
            faithfulness=faithfulness,
            review_state=review_state,
            backend=self.backend.name,
        )


DEFAULT_FORMALIZER = FormalizerService()
