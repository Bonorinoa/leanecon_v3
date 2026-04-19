"""Lean Econ v3 configuration."""

from __future__ import annotations

import os
from pathlib import Path

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
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "3600"))
JOB_MAX_CONCURRENT = int(os.getenv("JOB_MAX_CONCURRENT", "2"))

PLANNER_MODEL = os.getenv("LEANECON_PLANNER_MODEL", "hf:Qwen2.5-72B")
FORMALIZER_MODEL = os.getenv("LEANECON_FORMALIZER_MODEL", "hf:mistralai/Leanstral-2603")
PROVER_MODEL = os.getenv("LEANECON_PROVER_MODEL", "hf:Goedel-LM/Goedel-Prover-V2-32B")
HF_TOKEN = os.getenv("HF_TOKEN", "")

JOB_STATES = {
    "queued",
    "awaiting_plan_review",
    "awaiting_formalization_review",
    "running_prover",
    "completed",
    "failed",
}
