"""Budget profile policy for release, frontier, and research runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Mapping


BUDGET_PROFILE_ENV = "LEANECON_BUDGET_PROFILE"


@dataclass(frozen=True)
class ProviderPolicy:
    planner_provider: str
    planner_model: str
    formalizer_provider: str
    formalizer_model: str
    prover_provider: str
    prover_model: str
    strategy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BudgetProfile:
    name: str
    description: str
    max_prover_turns: int
    max_prove_steps: int
    max_total_tool_calls: int
    max_search_tool_calls: int
    max_search_tool_calls_hybrid: int
    max_timeout_seconds: int
    target_timeout_caps: dict[str, int]
    direct_close_attempt_cap: int
    mathlib_native_direct_close_attempt_cap: int
    allow_frontier_claims: bool
    allow_mathlib_native: bool
    allow_non_mistral_provider: bool
    allow_provider_fallback: bool
    local_only: bool
    release_metrics_eligible: bool
    provider_policy: ProviderPolicy

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_policy"] = self.provider_policy.to_dict()
        return payload

    def public_dict(self) -> dict[str, Any]:
        """Return the operational fields that should appear in artifacts."""

        return {
            "name": self.name,
            "description": self.description,
            "max_prover_turns": self.max_prover_turns,
            "max_prove_steps": self.max_prove_steps,
            "max_total_tool_calls": self.max_total_tool_calls,
            "max_search_tool_calls": self.max_search_tool_calls,
            "max_search_tool_calls_hybrid": self.max_search_tool_calls_hybrid,
            "max_timeout_seconds": self.max_timeout_seconds,
            "target_timeout_caps": dict(self.target_timeout_caps),
            "direct_close_attempt_cap": self.direct_close_attempt_cap,
            "mathlib_native_direct_close_attempt_cap": self.mathlib_native_direct_close_attempt_cap,
            "allow_frontier_claims": self.allow_frontier_claims,
            "allow_mathlib_native": self.allow_mathlib_native,
            "allow_non_mistral_provider": self.allow_non_mistral_provider,
            "allow_provider_fallback": self.allow_provider_fallback,
            "local_only": self.local_only,
            "release_metrics_eligible": self.release_metrics_eligible,
            "provider_policy": self.provider_policy.to_dict(),
        }


_MISTRAL_ALPHA_POLICY = ProviderPolicy(
    planner_provider="mistral",
    planner_model="mistral-large-2512",
    formalizer_provider="mistral",
    formalizer_model="labs-leanstral-2603",
    prover_provider="mistral",
    prover_model="labs-leanstral-2603",
    strategy="mistral_primary_alpha_release",
)

_PROFILES: dict[str, BudgetProfile] = {
    "release": BudgetProfile(
        name="release",
        description="Narrow public-alpha profile for release/API paths.",
        max_prover_turns=8,
        max_prove_steps=32,
        max_total_tool_calls=40,
        max_search_tool_calls=12,
        max_search_tool_calls_hybrid=16,
        max_timeout_seconds=300,
        target_timeout_caps={
            "theorem_body": 300,
            "subgoal": 180,
            "apollo_lemma": 120,
        },
        direct_close_attempt_cap=24,
        mathlib_native_direct_close_attempt_cap=2,
        allow_frontier_claims=False,
        allow_mathlib_native=False,
        allow_non_mistral_provider=False,
        allow_provider_fallback=False,
        local_only=False,
        release_metrics_eligible=True,
        provider_policy=_MISTRAL_ALPHA_POLICY,
    ),
    "frontier": BudgetProfile(
        name="frontier",
        description="Expanded bounded profile for diagnostic frontier attempts.",
        max_prover_turns=12,
        max_prove_steps=48,
        max_total_tool_calls=80,
        max_search_tool_calls=20,
        max_search_tool_calls_hybrid=24,
        max_timeout_seconds=600,
        target_timeout_caps={
            "theorem_body": 450,
            "subgoal": 240,
            "apollo_lemma": 180,
        },
        direct_close_attempt_cap=24,
        mathlib_native_direct_close_attempt_cap=2,
        allow_frontier_claims=True,
        allow_mathlib_native=True,
        allow_non_mistral_provider=True,
        allow_provider_fallback=True,
        local_only=False,
        release_metrics_eligible=False,
        provider_policy=ProviderPolicy(
            **{
                **_MISTRAL_ALPHA_POLICY.to_dict(),
                "strategy": "mistral_primary_non_release_fallback_allowed",
            }
        ),
    ),
    "research": BudgetProfile(
        name="research",
        description="Broad local-only experimental profile.",
        max_prover_turns=20,
        max_prove_steps=96,
        max_total_tool_calls=160,
        max_search_tool_calls=40,
        max_search_tool_calls_hybrid=48,
        max_timeout_seconds=1200,
        target_timeout_caps={
            "theorem_body": 900,
            "subgoal": 600,
            "apollo_lemma": 300,
        },
        direct_close_attempt_cap=48,
        mathlib_native_direct_close_attempt_cap=4,
        allow_frontier_claims=True,
        allow_mathlib_native=True,
        allow_non_mistral_provider=True,
        allow_provider_fallback=True,
        local_only=True,
        release_metrics_eligible=False,
        provider_policy=ProviderPolicy(
            **{
                **_MISTRAL_ALPHA_POLICY.to_dict(),
                "strategy": "local_only_non_release_experimental",
            }
        ),
    ),
}


def available_budget_profiles() -> tuple[str, ...]:
    return tuple(_PROFILES)


def resolve_budget_profile(
    value: str | None = None,
    *,
    runtime_env: str | None = None,
) -> BudgetProfile:
    selected = (value or os.getenv(BUDGET_PROFILE_ENV) or "release").strip().lower()
    if selected not in _PROFILES:
        allowed = ", ".join(available_budget_profiles())
        raise ValueError(
            f"Invalid budget profile `{selected}`. Expected one of: {allowed}."
        )
    profile = _PROFILES[selected]
    if profile.local_only and (runtime_env or "local").strip().lower() != "local":
        raise ValueError(
            f"Budget profile `{profile.name}` is local-only and cannot run with "
            f"LEANECON_ENV={runtime_env!r}."
        )
    return profile


def active_budget_profile(*, runtime_env: str | None = None) -> BudgetProfile:
    return resolve_budget_profile(runtime_env=runtime_env)


def clamp_int(value: int, cap: int, *, floor: int = 1) -> int:
    return max(floor, min(int(value), int(cap)))


def clamp_target_timeouts(
    timeouts: Mapping[str, int | None],
    profile: BudgetProfile,
) -> dict[str, int]:
    clamped: dict[str, int] = {}
    for key, cap in profile.target_timeout_caps.items():
        requested = timeouts.get(key)
        clamped[key] = clamp_int(int(requested or cap), int(cap))
    return clamped


def _stage_violation(stage: str, actual: Mapping[str, str], expected_provider: str, expected_model: str) -> str | None:
    provider = str(actual.get("provider") or "").strip()
    model = str(actual.get("model") or "").strip()
    if provider != expected_provider or model != expected_model:
        return (
            f"{stage} must use {expected_provider}:{expected_model} under the release "
            f"profile, got {provider or 'unknown'}:{model or 'unknown'}."
        )
    return None


def evaluate_provider_guardrail(
    profile: BudgetProfile,
    stages: Mapping[str, Mapping[str, str]],
    *,
    justification: str | None = None,
) -> dict[str, Any]:
    """Evaluate whether the provider/model posture is release-compliant."""

    violations: list[str] = []
    warnings: list[str] = []
    policy = profile.provider_policy

    if profile.name == "release":
        for stage, provider, model in (
            ("planner", policy.planner_provider, policy.planner_model),
            ("formalizer", policy.formalizer_provider, policy.formalizer_model),
            ("prover", policy.prover_provider, policy.prover_model),
        ):
            violation = _stage_violation(stage, stages.get(stage, {}), provider, model)
            if violation is not None:
                violations.append(violation)
        fallback = stages.get("prover_fallback")
        if fallback is not None:
            fallback_violation = _stage_violation(
                "prover_fallback",
                fallback,
                policy.prover_provider,
                policy.prover_model,
            )
            if fallback_violation is not None:
                violations.append(
                    "Non-Mistral prover fallback is not allowed under the release profile. "
                    + fallback_violation
                )
    else:
        non_mistral = {
            stage: dict(payload)
            for stage, payload in stages.items()
            if str(payload.get("provider") or "").strip() != "mistral"
        }
        if non_mistral:
            warnings.append(
                "Non-Mistral provider path is marked non-release by the active "
                f"`{profile.name}` budget profile."
            )
            if justification:
                warnings.append(f"justification: {justification}")

    return {
        "budget_profile": profile.name,
        "strategy": policy.strategy,
        "release_profile": profile.name == "release",
        "release_compliant": not violations,
        "release_metrics_eligible": profile.release_metrics_eligible and not violations,
        "non_release_provider_path": profile.name != "release",
        "allow_non_mistral_provider": profile.allow_non_mistral_provider,
        "allow_provider_fallback": profile.allow_provider_fallback,
        "stages": {key: dict(value) for key, value in stages.items()},
        "violations": violations,
        "warnings": warnings,
    }
