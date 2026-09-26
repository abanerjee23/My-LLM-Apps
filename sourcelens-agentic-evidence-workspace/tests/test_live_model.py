"""Real model calls. Skipped unless run with `make test-live` (costs money per run)."""
import pytest
from sqlalchemy import text

from sourcelens.investigator import Investigator, new_investigation
from sourcelens.observability import configure_galileo


@pytest.mark.live_model
@pytest.mark.asyncio
async def test_live_agents_complete_an_owned_investigation(settings, store, investigator, make_user):
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY is not set")
    assert configure_galileo(settings)
    live = Investigator(
        settings=settings.model_copy(update={"sourcelens_live_agent": True}),
        store=store,
        warehouse=investigator.warehouse,
        evidence_index=investigator.evidence_index,
    )
    user = make_user()
    investigation = new_investigation(
        "Why did revenue and sentiment decline for Nova X300 in the latest quarter?"
    )
    store.create_investigation(investigation, user.user_id)
    await live.run(investigation.investigation_id, user.user_id)
    result = store.get_investigation(investigation.investigation_id, user.user_id)

    assert result.status == "ready"
    assert any(event.event_type == "agent" for event in result.events)
    cited = {evidence_id for finding in result.findings for evidence_id in finding.evidence_ids}
    known = {item.evidence_id for item in result.evidence} | {item.artifact_id for item in result.artifacts}
    assert cited <= known
    with store.engine.connect() as db:
        usage = db.execute(
            text(
                """
                SELECT input_tokens, output_tokens, estimated_cost, duration_ms
                FROM usage_ledger
                WHERE investigation_id = :investigation_id
                ORDER BY created_at
                """
            ),
            {"investigation_id": investigation.investigation_id},
        ).all()
    assert len(usage) == 3  # Planner, Evidence Analyst, and Lead Investigator.
    assert all(row.input_tokens > 0 and row.output_tokens > 0 for row in usage)
    assert all(row.estimated_cost > 0 and row.duration_ms and row.duration_ms > 0 for row in usage)
