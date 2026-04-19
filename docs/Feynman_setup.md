# Feynman Research & Audit Agent Setup (v3)
**Date:** 19 April 2026  
**Purpose:** Local 30B+ class model for research, auditing, gap analysis, and grounded brainstorming. Zero rate limits, full context, private.

## Recommended Model (April 2026)
- **Primary**: `qwen2.5-coder:32b` or `deepseek-r1:32b` (Ollama) — best balance of reasoning + coding for Lean/econ tasks.
- **Alternative**: `llama3.3:70b` or `gemma3:27b` if VRAM limited.
- **Why not smaller?** 30B+ is the minimum where HILBERT-style informal reasoning + self-correction becomes reliable for economic claims.

## Setup (Local Machine — 5 minutes)
```bash
# 1. Install Ollama (if not already)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull 32B+ model (first time ~20–40 GB download)
ollama pull qwen2.5-coder:32b

# 3. Run with good context + keep-alive
ollama run qwen2.5-coder:32b --keepalive 60m
```

## Integration with Codex CLI (Fallback)
In VSCode settings or Codex config:
```json
{
  "codex.fallbackProvider": "ollama",
  "codex.ollamaModel": "qwen2.5-coder:32b",
  "codex.ollamaUrl": "http://localhost:11434"
}
```

When Codex hits token limit (rare), it seamlessly falls back to Feynman.

## Usage Patterns (as Research/Audit Agent)
- **Gap analysis**: "Compare this v3 scaffold against HILBERT paper (arxiv:2509.22819) and v2 engineering log. List 5 concrete improvements."
- **Prompt auditing**: "Review this Planner prompt. Does it follow the hilbert_protocol.md rubric? Suggest 3 fixes."
- **Benchmark diagnosis**: "Why did tier2_frontier claim #7 fail? Propose 2 Lean 4 fixes using Preamble concepts."
- **Sprint planning**: "Break down next 2 weeks into 8 atomic tasks for Codex 5.4 with clear acceptance criteria."

## Why HF for Production, Ollama for Feynman?
- **HF (Leanstral, Goedel-Prover-V2)**: Reproducible, versioned, serverless endpoints, no GPU ops burden on Railway/Docker in early stages.
- **Ollama (Feynman)**: Full 32B+ weights locally, unlimited tokens, private traces, perfect for iterative research where context is 50k+ tokens.

**We keep the expensive frontier models in the loop only where they add unique value (Planner). Everything else is open, local, or HF.**

— Grok, CTO | 19 April 2026