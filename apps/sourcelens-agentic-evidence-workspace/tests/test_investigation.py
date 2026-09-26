import pytest

from sourcelens.investigator import new_investigation
from sourcelens.models import DataSource


@pytest.mark.asyncio
async def test_reference_quality_scenario_is_investigated(services, make_user):
    store, investigator = services
    user = make_user()
    investigation = new_investigation(
        "Why did revenue and sentiment decline for Nova X300 in the latest quarter?"
    )
    store.create_investigation(investigation, user.user_id)

    await investigator.run(investigation.investigation_id, user.user_id)
    result = store.get_investigation(investigation.investigation_id, user.user_id)

    assert result is not None
    assert result.status == "ready"
    assert "quality" in result.executive_summary.lower()
    assert len(result.findings) == 3
    assert any(item.source_query_id == "q-revenue" for item in result.artifacts)
    assert result.evidence
    assert all(item.metadata["raw_hash"] for item in result.evidence)


@pytest.mark.asyncio
async def test_uploaded_source_is_profiled_and_cited(services, make_user):
    store, investigator = services
    user = make_user()
    source = DataSource(
        source_id=f"file-customer-feedback-{user.user_id}",
        name="customer-feedback.csv",
        kind="file.csv",
        record_count=4,
        metadata={"sha256": "abc123"},
    )
    store.save_source(
        source,
        user.user_id,
        [
            {"region": "North", "sentiment": "negative", "score": "2"},
            {"region": "North", "sentiment": "negative", "score": "1"},
            {"region": "South", "sentiment": "positive", "score": "5"},
            {"region": "North", "sentiment": "neutral", "score": "3"},
        ],
    )
    investigation = new_investigation("What patterns are present in customer-feedback.csv?")
    store.create_investigation(investigation, user.user_id)

    await investigator.run(investigation.investigation_id, user.user_id)
    result = store.get_investigation(investigation.investigation_id, user.user_id)

    assert result is not None
    assert result.status == "ready"
    assert result.scope["source_id"] == source.source_id
    assert result.scope["source_version_id"] == source.version_id
    assert any(item.artifact_id == "source-profile" for item in result.artifacts)
    assert result.evidence[0].source_id == source.source_id
    assert all(finding.evidence_ids for finding in result.findings)


@pytest.mark.asyncio
async def test_action_events_are_persisted(services, make_user):
    store, investigator = services
    user = make_user()
    investigation = new_investigation("Investigate Nova X300 performance")
    store.create_investigation(investigation, user.user_id)
    await investigator.run(investigation.investigation_id, user.user_id)
    result = store.get_investigation(investigation.investigation_id, user.user_id)

    assert [event.event_type for event in result.events] == [
        "scope",
        "query",
        "query",
        "evidence",
        "brief",
    ]
