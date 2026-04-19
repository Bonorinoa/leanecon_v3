from __future__ import annotations

import json

from evals.local_gate import run_claim_set


def test_local_gate_uses_threshold_counts() -> None:
    tier1 = run_claim_set("tier1_core")
    tier2 = run_claim_set("tier2_frontier")

    assert tier1["passes"] == 22
    assert tier1["total"] == 23
    assert round(float(tier2["pass_at_1"]), 3) == 0.615
