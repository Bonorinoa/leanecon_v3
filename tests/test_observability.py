from __future__ import annotations

from src.api.jobs import JobStore
from src.observability.models import AuditEvent, StageTiming
from src.observability.pricing import complete_usage, lookup_pricing


def test_pricing_lookup_and_complete_usage(monkeypatch) -> None:
    import src.observability.pricing as pricing_module

    monkeypatch.setattr(pricing_module, "COST_TRACKING_ENABLED", True)
    usage = complete_usage(
        stage="formalizer",
        provider="mistral",
        model="labs-leanstral-2603",
        latency_ms=12.5,
        success=True,
        prompt_text="abc def ghi",
        response_text="lemma foo := by trivial",
    )

    assert lookup_pricing("mistral", "labs-leanstral-2603") is not None
    assert lookup_pricing("ollama", "gemma4:31b-cloud") is not None
    assert usage.input_tokens is not None
    assert usage.output_tokens is not None
    assert usage.estimated_cost_usd == 0.0

    monkeypatch.setattr(pricing_module, "COST_TRACKING_ENABLED", False)
    disabled = complete_usage(
        stage="planner",
        provider="huggingface",
        model="MiniMaxAI/MiniMax-M2.7",
        latency_ms=5.0,
        success=True,
        prompt_text="hello world",
        response_text="ok",
    )
    assert disabled.estimated_cost_usd is None


def test_job_store_persists_usage_metrics_and_audits(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db", ttl_seconds=3600)
    job = store.create(status="queued", review_state="queued", result={"claim": "demo"})
    store.record_stage_usage(
        job.id,
        complete_usage(
            stage="formalizer",
            provider="mistral",
            model="labs-leanstral-2603",
            latency_ms=7.0,
            success=True,
            prompt_text="claim",
            response_text="formalized claim",
        ),
    )
    store.record_timing(job.id, StageTiming(formalizer_ms=7.0, total_ms=7.0))
    store.record_audit_event(
        job.id,
        AuditEvent(
            stage="formalizer",
            event_type="stage_completed",
            provider="mistral",
            model="labs-leanstral-2603",
            success=True,
            metadata={"claim_id": "demo"},
        ),
    )
    hydrated = store.get(job.id)
    assert hydrated is not None
    assert hydrated.result is not None
    assert hydrated.result["usage_by_stage"]["formalizer"]["stage"] == "formalizer"
    assert hydrated.result["timing_breakdown"]["formalizer_ms"] == 7.0
    assert hydrated.result["audit_summary"]["event_count"] == 1

    snapshot = store.metrics_snapshot()
    assert snapshot["usage_totals"]["records"] == 1
    assert snapshot["usage_by_stage"]["formalizer"]["records"] == 1
