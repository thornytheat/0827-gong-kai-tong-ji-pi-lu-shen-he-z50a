from app.business import decision
from app.modules.publication import assess_publication, cross_batch_check  # noqa: F401


def policy(result):
    return decision(result,'MARGINAL_INFERENCE' if result['flag'] else None)


def combined_policy(current=None, published=None, **kwargs):
    """Policy-language alias for cross-batch adjudication."""
    return assess_publication(current, published, **kwargs)


cross_batch_policy = combined_policy
cross_batch = combined_policy
combined = combined_policy
publication_policy = combined_policy
