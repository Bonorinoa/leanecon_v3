FROM ghcr.io/bonorinoa/leanecon-lean-base:latest AS lean

FROM python:3.11-slim AS app
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY evals ./evals
RUN pip install --no-cache-dir -e .

COPY --from=lean /lean_workspace /app/lean_workspace
COPY docs ./docs
COPY skills ./skills
COPY benchmark_baselines ./benchmark_baselines
COPY .github ./.github
COPY railway.toml ./

ENV HF_HOME=/root/.cache/huggingface
RUN python - <<'PY'
from pathlib import Path

cache_root = Path("/root/.cache/huggingface")
cache_root.mkdir(parents=True, exist_ok=True)
(cache_root / "TODO_MODELS.txt").write_text(
    "TODO(Grok/CTO): prefetch Leanstral and Goedel-Prover-V2 weights here for self-hosted builds.\n",
    encoding="utf-8",
)
PY

EXPOSE 8000
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
