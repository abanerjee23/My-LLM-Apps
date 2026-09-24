from sourcelens.investigator import new_investigation
from sourcelens.models import Decision, InvestigationStatus, ReviewRequest


def test_review_creates_immutable_notebook_revisions(services):
    store, _ = services
    investigation = new_investigation("Investigate Nova X300 performance")
    investigation.status = InvestigationStatus.READY
    investigation.executive_summary = "Original"
    store.save_investigation(investigation)

    first = store.save_review(
        investigation,
        ReviewRequest(decision=Decision.ACCEPTED, accuracy_rating=4, usefulness_rating=5),
    )
    second = store.save_review(
        investigation,
        ReviewRequest(
            decision=Decision.EDITED_ACCEPTED,
            edited_summary="Edited",
            accuracy_rating=5,
            usefulness_rating=5,
        ),
    )

    assert first.revision == 1
    assert second.revision == 2
    assert first.summary == "Original"
    assert second.summary == "Edited"
