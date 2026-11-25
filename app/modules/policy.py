from app.business import decision


def policy(result):
    return decision(result,'MARGINAL_INFERENCE' if result['flag'] else None)
