from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config import load_runtime_env, validate_runtime_secrets


def test_load_runtime_env_local_overrides_existing_env(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")

    monkeypatch.setenv("LEANECON_ENV", "local")
    monkeypatch.setenv("HF_TOKEN", "hf_inherited")

    resolved = load_runtime_env(env_path=env_path)

    assert resolved == "local"
    assert "hf_from_file" == os.environ["HF_TOKEN"]


def test_load_runtime_env_non_local_preserves_exported_env(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")

    monkeypatch.setenv("LEANECON_ENV", "prod")
    monkeypatch.setenv("HF_TOKEN", "hf_exported")

    resolved = load_runtime_env(env_path=env_path)

    assert resolved == "prod"
    assert "hf_exported" == os.environ["HF_TOKEN"]


def test_validate_runtime_secrets_allows_local_missing_credentials() -> None:
    validate_runtime_secrets(
        runtime_env="local",
        planner_backend="hf-structured",
        planner_model="OBLITERATUS/gemma-4-E4B-it-OBLITERATED",
        planner_provider="auto",
        prover_backend="leanstral",
        prover_provider="auto",
        formalizer_backend="leanstral",
        hf_token="",
        mistral_api_key="",
        ollama_api_key="",
    )


def test_validate_runtime_secrets_requires_active_non_local_credentials() -> None:
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        validate_runtime_secrets(
            runtime_env="prod",
            planner_backend="hf-structured",
            planner_model="OBLITERATUS/gemma-4-E4B-it-OBLITERATED",
            planner_provider="auto",
            prover_backend="leanstral",
            prover_provider="auto",
            formalizer_backend="leanstral",
            hf_token="",
            mistral_api_key="",
            ollama_api_key="",
        )


def test_validate_runtime_secrets_requires_ollama_key_for_non_local_ollama_planner() -> None:
    with pytest.raises(RuntimeError, match="OLLAMA_API_KEY"):
        validate_runtime_secrets(
            runtime_env="prod",
            planner_backend="ollama-cloud",
            planner_model="gemma4:31b",
            planner_provider="ollama",
            prover_backend="leanstral",
            prover_provider="auto",
            formalizer_backend="leanstral",
            hf_token="hf_valid",
            mistral_api_key="mistral_valid",
            ollama_api_key="",
        )


def test_validate_runtime_secrets_requires_mistral_key_for_non_local_mistral_planner() -> None:
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        validate_runtime_secrets(
            runtime_env="prod",
            planner_backend="mistral-structured",
            planner_model="mistral-large-2512",
            planner_provider="mistral",
            prover_backend="leanstral",
            prover_provider="mistral",
            formalizer_backend="leanstral",
            hf_token="hf_valid",
            mistral_api_key="",
            ollama_api_key="ollama_valid",
        )
