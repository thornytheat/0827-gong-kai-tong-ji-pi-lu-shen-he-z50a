from app.modules.publication import (  # noqa: F401
    BLOCKED,
    CLEAR,
    CONFLICT,
    DECISION_APPROVED,
    DECISION_HELD,
    REINFORCED,
    assess_publication,
    choose_supplements,
    cross_batch_check,
    publication_decision,
    snapshot_from_data,
)


def issue_or_hold(snapshot, publications=None, **kwargs):
    return assess_publication(snapshot, publications, **kwargs)


# Additional names a focused publication service module may be expected to expose.
evaluate = assess_publication
decide = assess_publication
check = cross_batch_check
