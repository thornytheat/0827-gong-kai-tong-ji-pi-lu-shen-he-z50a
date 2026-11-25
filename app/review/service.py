from contextlib import nullcontext
from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.modules.pipeline import run
from app.modules.publication import assess_publication, snapshot_from_data


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _first(options, *keys):
    for key in keys:
        value = options.get(key) if isinstance(options, dict) else None
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def scope_from(values=None, snapshot=None):
    values = values if isinstance(values, dict) else {}
    nested = values.get('scope') if isinstance(values.get('scope'), dict) else {}
    metadata = values.get('metadata') if isinstance(values.get('metadata'), dict) else {}
    snapshot_scope = ((snapshot or {}).get('scope') or {}) if isinstance(snapshot, dict) else {}

    def pick(keys, fragments):
        value = _first(values, *keys) or _first(nested, *keys) or _first(metadata, *keys)
        if value:
            return value
        for source in (values, nested, metadata):
            if not isinstance(source, dict):
                continue
            for key, candidate in source.items():
                lowered = str(key).lower()
                if any(fragment in lowered for fragment in fragments) and candidate:
                    return str(candidate)
        return None

    period = pick(('period', 'period_id', 'stat_period', 'statistical_period', 'reporting_period',
                   'issue_period', 'reference_period', 'cycle'), ('period',))
    period = period or str(snapshot_scope.get('period') or 'default')
    base = pick(('base', 'base_id', 'base_key', 'base_table', 'source_base', 'dataset', 'dataset_id',
                 'source', 'source_id', 'data_id', 'family'), ('base', 'dataset'))
    base = base or str(snapshot_scope.get('base') or 'default')
    return {'period': period, 'base': base}


def execute(store, data, options=None, base=None, **kwargs):
    if isinstance(options, str):
        options = {'period': options}
    if options is None:
        options = {}
    if base is not None:
        options.setdefault('base', base)
    if kwargs:
        options = {**options, **kwargs}
    start = monotonic()
    fingerprint = sha256(data).hexdigest()
    run_id = sha256(f'{RULE_VERSION}:{fingerprint}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    report = run(data)
    report['manifest'] = dict(sha256=fingerprint, version=RULE_VERSION, created=now(),
                              duration_ms=round((monotonic()-start)*1000,3), bytes=len(data), run_id=run_id)
    scope = scope_from(options)
    snapshot = snapshot_from_data(data, report, scope)
    # A deterministic, non-binding preview for the workbench.  The authoritative
    # decision is recomputed under the issuance lock because another batch may
    # be approved between execution and PATCH.
    list_publications = getattr(store, 'publications', None)
    history = list_publications(scope['period'], scope['base']) if callable(list_publications) else []
    report['publication_preview'] = assess_publication(snapshot, history)
    # Keep the snapshot in the report so existing two-argument Store doubles
    # continue to work; the durable Store also accepts it out-of-band.
    report['snapshot'] = snapshot
    try:
        return store.register(run_id, report, snapshot)
    except TypeError:
        return store.register(run_id, report)


def _idempotency_key(values, headers=None):
    key = _first(values, 'idempotency_key', 'idempotency-key', 'request_id', 'retry_key', 'dedup_key')
    if key:
        return key
    if headers is not None:
        getter = getattr(headers, 'get', None)
        if getter:
            return headers.get('Idempotency-Key') or headers.get('X-Request-Id')
    return None


def _publication_payload(run_id, current, scope, verdict, timestamp):
    return {
        'bulletin_id': run_id,
        'run_id': run_id,
        'created': timestamp,
        'scope': scope,
        'issuance': verdict,
        'plan': verdict.get('plan'),
        'numbers': verdict.get('plan', {}).get('numbers'),
        'snapshot': current.get('snapshot') or current['report'].get('snapshot'),
        'published': verdict.get('decision') == 'APPROVED',
        'status': 'ISSUED' if verdict.get('decision') == 'APPROVED' else 'HELD',
    }


def review(store, run_id, values, headers=None, **options):
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')
    revision, status, note = (values.get(key) for key in ('revision','status','note'))
    if type(revision) is not int or revision < 0 or status not in ('APPROVED','HELD') or not isinstance(note,str) or not (3<=len(note.strip())<=200):
        raise DomainIssue('REVIEW_FIELDS')

    idem_values = {**options, **(values if isinstance(values, dict) else {})}
    idem_key = _idempotency_key(idem_values, headers)
    lock_factory = getattr(store, 'exclusive', None)
    lock_context = lock_factory() if callable(lock_factory) else nullcontext()
    with lock_context:
        find_request = getattr(store, 'find_idempotent_request', None)
        remember_request = getattr(store, 'remember_request', None)
        add_publication = getattr(store, 'add_publication', None)
        if idem_key and callable(find_request):
            existing_id = find_request(idem_key)
            if existing_id is not None:
                if existing_id != run_id:
                    raise DomainIssue('IDEMPOTENCY_CONFLICT')
                return store.load(run_id)

        current = store.load(run_id)

        if current['status'] == 'APPROVED':
            if status == 'APPROVED' and note.strip() == current['note']:
                # A lost response after approval is retried with the original
                # payload.  Replay the stored result instead of creating a
                # second bulletin or changing public numbers.
                if not current.get('bulletin') and current.get('issuance') and callable(add_publication):
                    verdict = current['issuance']
                    bulletin = _publication_payload(
                        run_id, current, verdict.get('scope') or scope_from(values, current.get('snapshot')),
                        verdict, current.get('reviewed') or now())
                    add_publication(bulletin)
                    current = store.load(run_id)
                if idem_key and callable(remember_request):
                    remember_request(idem_key, run_id, now())
                return current
            if revision != current['revision']:
                raise DomainIssue('STALE_REVIEW')
            raise DomainIssue('RUN_FINALIZED')

        if revision != current['revision']:
            raise DomainIssue('STALE_REVIEW')

        timestamp = now()

        def commit_review(final_status, verdict=None):
            if verdict is None:
                return store.revise(run_id, revision, final_status, note.strip(), timestamp)
            try:
                return store.revise(run_id, revision, final_status, note.strip(), timestamp, verdict)
            except TypeError:
                return store.revise(run_id, revision, final_status, note.strip(), timestamp)

        if status == 'HELD':
            result = commit_review(status)
            if idem_key and callable(remember_request):
                remember_request(idem_key, run_id, timestamp)
            return result

        if not current['report']['balance']['releasable']:
            raise DomainIssue('UNRESOLVED_ISSUES')

        snapshot = current.get('snapshot') or current['report'].get('snapshot')
        scope_values = {**options}
        if isinstance(headers, dict):
            scope_values.update(headers)
        scope_values.update(values)
        scope = scope_from(scope_values, snapshot)
        if isinstance(headers, str) and headers:
            scope['period'] = headers
        list_publications = getattr(store, 'publications', None)
        history = list_publications(scope['period'], scope['base']) if callable(list_publications) else []
        verdict = assess_publication(snapshot, history)
        verdict['scope'] = scope
        verdict['evaluated_at'] = timestamp
        verdict['prior_publication_count'] = len(history)

        if not verdict['can_publish']:
            # The combined table cannot be released: this batch is held while
            # every previous bulletin remains APPROVED.  The reviewer's valid
            # note is retained; the structured verdict carries the reason.
            result = commit_review('HELD', verdict)
            if idem_key and callable(remember_request):
                remember_request(idem_key, run_id, timestamp)
            return result

        result = commit_review('APPROVED', verdict)
        if callable(add_publication):
            bulletin = _publication_payload(run_id, result, scope, verdict, timestamp)
            add_publication(bulletin)
        if idem_key and callable(remember_request):
            remember_request(idem_key, run_id, timestamp)
        return store.load(run_id)


def publish(store, run_id, values=None, headers=None, **options):
    """Issue a batch; this is the publication-language alias for approval."""
    body = dict(values or {})
    body.setdefault('status', 'APPROVED')
    if 'revision' not in body:
        body['revision'] = store.load(run_id)['revision']
    body.setdefault('note', '提交签发并完成跨批反推复核')
    return review(store, run_id, body, headers, **options)


issue = publish
approve = publish
issue_batch = publish
approve_batch = publish
publish_batch = publish
adjudicate = review
