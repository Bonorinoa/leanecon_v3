"""Lean Econ v3 configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

APP_VERSION = "3.0.0-alpha"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAN_WORKSPACE = PROJECT_ROOT / "lean_workspace"
LEAN_PROOF_DIR = LEAN_WORKSPACE / "LeanEcon"
PREAMBLE_DIR = LEAN_PROOF_DIR / "Preamble"
SKILLS_DIR = PROJECT_ROOT / "skills"
EVAL_CLAIMS_DIR = PROJECT_ROOT / "evals" / "claim_sets"
BENCHMARK_BASELINE_DIR = PROJECT_ROOT / "benchmark_baselines" / "v3_alpha"
CACHE_DIR = PROJECT_ROOT / ".cache"
DB_PATH = CACHE_DIR / "jobs.db"
MEMORY_DB_PATH = CACHE_DIR / "memory.db"

_PLACEHOLDER_MARKERS = (
    "replace-with",
    "changeme",
    "example",
    "your-",
    "<",
    ">",
)


def _normalize_runtime_env(value: str | None) -> str:
    cleaned = (value or "local").strip().lower()
    return cleaned or "local"


def load_runtime_env(*, env_path: Path | None = None) -> str:
    """Load the repo `.env` before reading runtime configuration."""

    resolved_path = env_path or (PROJECT_ROOT / ".env")
    initial_mode = _normalize_runtime_env(os.environ.get("LEANECON_ENV"))
    load_dotenv(resolved_path, override=initial_mode == "local")
    return _normalize_runtime_env(os.environ.get("LEANECON_ENV", initial_mode))


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


def _hf_token_invalid(value: str) -> bool:
    stripped = value.strip()
    return not stripped or _looks_like_placeholder(stripped) or not stripped.startswith("hf_")


def _mistral_key_invalid(value: str) -> bool:
    stripped = value.strip()
    return not stripped or _looks_like_placeholder(stripped)


def _ollama_key_invalid(value: str) -> bool:
    stripped = value.strip()
    return not stripped or _looks_like_placeholder(stripped)


def validate_runtime_secrets(
    *,
    runtime_env: str,
    planner_backend: str,
    planner_model: str,
    planner_provider: str,
    prover_backend: str,
    prover_provider: str,
    formalizer_backend: str,
    hf_token: str,
    mistral_api_key: str,
    ollama_api_key: str,
) -> None:
    """Raise early for missing secrets in non-local environments."""

    if runtime_env == "local":
        return

    failures: list[str] = []

    if planner_backend in {"hf-structured", "minimax-m2.7", "trinity-large-thinking", "gemma-4-31b-it"}:
        if _hf_token_invalid(hf_token):
            failures.append(f"HF_TOKEN is required for planner backend `{planner_backend}` using model `{planner_model}`.")
    if planner_backend == "mistral-structured" and _mistral_key_invalid(mistral_api_key):
        failures.append(
            f"MISTRAL_API_KEY is required for planner backend `{planner_backend}` using model `{planner_model}`."
        )
    if planner_backend == "ollama-cloud" and _ollama_key_invalid(ollama_api_key):
        failures.append(
            f"OLLAMA_API_KEY is required for planner backend `{planner_backend}` using model `{planner_model}`."
        )

    prover_uses_hf = prover_backend == "goedel-prover-v2" or (
        prover_backend == "leanstral" and prover_provider not in {"", "auto", "mistral"}
    )
    if prover_uses_hf and _hf_token_invalid(hf_token):
        failures.append(f"HF_TOKEN is required for prover backend `{prover_backend}`.")

    if formalizer_backend == "leanstral" and _mistral_key_invalid(mistral_api_key):
        failures.append(f"MISTRAL_API_KEY is required for formalizer backend `{formalizer_backend}`.")

    if prover_backend == "leanstral" and prover_provider in {"", "auto", "mistral"} and _mistral_key_invalid(mistral_api_key):
        failures.append(f"MISTRAL_API_KEY is required for prover backend `{prover_backend}`.")

    if failures:
        joined = "\n- ".join(failures)
        raise RuntimeError(
            "Lean Econ configuration error: missing or invalid secrets for non-local mode.\n"
            f"- {joined}"
        )


LEANECON_ENV = load_runtime_env()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))
CORS_ORIGINS = [origin for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin]

LEAN_TIMEOUT = int(os.getenv("LEAN_TIMEOUT", "60"))
REPL_ENABLED = os.getenv("REPL_ENABLED", "true").lower() == "true"
FORMALIZER_REPL_VALIDATION_ENABLED = (
    os.getenv("FORMALIZER_REPL_VALIDATION_ENABLED", "true").lower() == "true"
)
MAX_PROVE_STEPS = int(os.getenv("MAX_PROVE_STEPS", "32"))
MAX_PROVE_TIMEOUT = int(os.getenv("MAX_PROVE_TIMEOUT", "300"))
MAX_TOTAL_TOOL_CALLS = int(os.getenv("MAX_TOTAL_TOOL_CALLS", "40"))
MAX_SEARCH_TOOL_CALLS = int(os.getenv("MAX_SEARCH_TOOL_CALLS", "12"))
# Sprint 23: hybrid (mathlib-native) mode gets a small budget bump to allow
# enrichment + second-retrieval rounds without starving the proof stage.
MAX_PROVE_STEPS_HYBRID = int(
    os.getenv("MAX_PROVE_STEPS_HYBRID", str(MAX_PROVE_STEPS + 4))
)
MAX_SEARCH_TOOL_CALLS_HYBRID = int(
    os.getenv("MAX_SEARCH_TOOL_CALLS_HYBRID", str(MAX_SEARCH_TOOL_CALLS + 2))
)
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "3600"))
JOB_MAX_CONCURRENT = int(os.getenv("JOB_MAX_CONCURRENT", "2"))
BENCHMARK_MAX_RECURSION_DEPTH = int(os.getenv("LEANECON_BENCHMARK_MAX_RECURSION_DEPTH", "1"))

PLANNER_BACKEND = os.getenv("LEANECON_PLANNER_BACKEND", "mistral-structured")
PLANNER_MODEL = os.getenv("LEANECON_PLANNER_MODEL", "mistral-large-2512")
PLANNER_PROVIDER = os.getenv("LEANECON_PLANNER_PROVIDER", "mistral").strip() or "mistral"
PLANNER_TIMEOUT = float(os.getenv("LEANECON_PLANNER_TIMEOUT", "120"))
OLLAMA_HOST = os.getenv("LEANECON_OLLAMA_HOST", "https://ollama.com").strip().rstrip("/")
EMBEDDING_MODEL = os.getenv("LEANECON_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
FORMALIZER_BACKEND = os.getenv("LEANECON_FORMALIZER_BACKEND", "leanstral")
FORMALIZER_MODEL = os.getenv("LEANECON_FORMALIZER_MODEL", "labs-leanstral-2603")
PROVER_BACKEND = os.getenv("LEANECON_PROVER_BACKEND", "leanstral").strip() or "leanstral"
PROVER_FALLBACK_BACKEND = (
    os.getenv("LEANECON_PROVER_FALLBACK_BACKEND", "leanstral").strip() or "leanstral"
)
PROVER_MODEL = os.getenv("LEANECON_PROVER_MODEL", "hf:Goedel-LM/Goedel-Prover-V2-32B")
PROVER_PROVIDER = os.getenv("LEANECON_PROVER_PROVIDER", "auto").strip() or "auto"
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "").strip()
MISTRAL_BASE_URL = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
FORMALIZER_TIMEOUT = float(os.getenv("LEANECON_FORMALIZER_TIMEOUT", "120"))
COST_TRACKING_ENABLED = os.getenv("LEANECON_COST_TRACKING_ENABLED", "true").lower() == "true"
BENCHMARK_REQUIRE_PRICING = os.getenv("LEANECON_BENCHMARK_REQUIRE_PRICING", "true").lower() == "true"
LIVE_MODEL_TESTS_ENABLED = os.getenv("LEANECON_LIVE_MODEL_TESTS", "false").lower() == "true"
PRICE_OVERRIDES_JSON_RAW = os.getenv("LEANECON_PRICE_OVERRIDES_JSON", "{}")
try:
    PRICE_OVERRIDES = json.loads(PRICE_OVERRIDES_JSON_RAW) if PRICE_OVERRIDES_JSON_RAW.strip() else {}
except json.JSONDecodeError:
    PRICE_OVERRIDES = {}

validate_runtime_secrets(
    runtime_env=LEANECON_ENV,
    planner_backend=PLANNER_BACKEND,
    planner_model=PLANNER_MODEL,
    planner_provider=PLANNER_PROVIDER,
    prover_backend=PROVER_BACKEND,
    prover_provider=PROVER_PROVIDER,
    formalizer_backend=FORMALIZER_BACKEND,
    hf_token=HF_TOKEN,
    mistral_api_key=MISTRAL_API_KEY,
    ollama_api_key=OLLAMA_API_KEY,
)

JOB_STATES = {
    "queued",
    "awaiting_plan_review",
    "awaiting_formalization_review",
    "running_prover",
    "completed",
    "failed",
}
