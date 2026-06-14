from __future__ import annotations

import pytest

from src.budget_profiles import evaluate_provider_guardrail, resolve_budget_profile


def test_budget_profile_defaults_to_release(monkeypatch) -> None:
    monkeypatch.delenv("LEANECON_BUDGET_PROFILE", raising=False)

    profile = resolve_budget_profile()

    assert profile.name == "release"
    assert profile.release_metrics_eligible is True
    assert profile.provider_policy.planner_model == "mistral-large-2512"
    assert profile.provider_policy.prover_model == "labs-leanstral-2603"


def test_invalid_budget_profile_is_rejected_clearly() -> None:
    with pytest.raises(ValueError, match="Invalid budget profile `banana`"):
        resolve_budget_profile("banana")


def test_release_caps_are_stricter_than_frontier_and_research() -> None:
    release = resolve_budget_profile("release")
    frontier = resolve_budget_profile("frontier")
    research = resolve_budget_profile("research")

    assert release.max_prover_turns < frontier.max_prover_turns < research.max_prover_turns
    assert release.max_total_tool_calls < frontier.max_total_tool_calls < research.max_total_tool_calls
    assert release.max_search_tool_calls < frontier.max_search_tool_calls < research.max_search_tool_calls
    assert release.max_timeout_seconds < frontier.max_timeout_seconds < research.max_timeout_seconds
    assert release.allow_mathlib_native is False
    assert frontier.allow_mathlib_native is True
    assert research.local_only is True


def test_provider_guardrail_blocks_non_mistral_release_fallback() -> None:
    release = resolve_budget_profile("release")

    guardrail = evaluate_provider_guardrail(
        release,
        {
            "planner": {"provider": "mistral", "model": "mistral-large-2512"},
            "formalizer": {"provider": "mistral", "model": "labs-leanstral-2603"},
            "prover": {"provider": "mistral", "model": "labs-leanstral-2603"},
            "prover_fallback": {
                "provider": "huggingface",
                "model": "Goedel-LM/Goedel-Prover-V2-32B",
            },
        },
    )

    assert guardrail["release_compliant"] is False
    assert guardrail["release_metrics_eligible"] is False
    assert any("Non-Mistral prover fallback" in item for item in guardrail["violations"])


def test_non_mistral_frontier_path_is_marked_non_release() -> None:
    frontier = resolve_budget_profile("frontier")

    guardrail = evaluate_provider_guardrail(
        frontier,
        {
            "planner": {"provider": "ollama", "model": "gemma4:31b-cloud"},
            "formalizer": {"provider": "mistral", "model": "labs-leanstral-2603"},
            "prover": {"provider": "huggingface", "model": "Goedel-LM/Goedel-Prover-V2-32B"},
        },
    )

    assert guardrail["release_profile"] is False
    assert guardrail["non_release_provider_path"] is True
    assert guardrail["warnings"]
