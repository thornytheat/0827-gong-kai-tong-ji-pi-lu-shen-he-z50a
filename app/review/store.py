import fcntl
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

from app.business import DomainIssue

SCHEMA = '''CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT
);
CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_verdicts (
    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_ledger (
    run_id TEXT PRIMARY KEY,
    bulletin_id TEXT NOT NULL UNIQUE,
    period TEXT NOT NULL,
    base_key TEXT NOT NULL,
    seq INTEGER NOT NULL,
    created TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotent_requests (
    idem_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS publications AS
    SELECT run_id AS id, run_id, bulletin_id, period, base_key AS base, seq, created, payload
    FROM publication_ledger;
CREATE VIEW IF NOT EXISTS bulletins AS SELECT * FROM publications;'''


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._lock_handle = None

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            yield db

    @contextmanager
    def exclusive(self):
        """Serialize issuance across threads and Store instances/processes."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._lock_handle is None:
                lock_path = self.path.with_name(self.path.name + '.issuance.lock')
                self._lock_handle = lock_path.open('a+')
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)

    def register(self, run_id, report, snapshot=None):
        with self.connect() as db:
            db.execute('INSERT INTO runs(id,report,created) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING',
                       (run_id, json.dumps(report, ensure_ascii=False), report['manifest']['created']))
            if snapshot is not None:
                db.execute('INSERT INTO run_snapshots(run_id,snapshot) VALUES (?,?) ON CONFLICT(run_id) DO NOTHING',
                           (run_id, json.dumps(snapshot, ensure_ascii=False, sort_keys=True)))
        return self.load(run_id)

    def _verdict(self, db, run_id):
        row = db.execute('SELECT payload FROM review_verdicts WHERE run_id=?', (run_id,)).fetchone()
        return json.loads(row['payload']) if row else None

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise DomainIssue('RUN_NOT_FOUND')
            result = dict(row)
            result['report'] = json.loads(result['report'])
            snapshot = db.execute('SELECT snapshot FROM run_snapshots WHERE run_id=?', (run_id,)).fetchone()
            if snapshot is not None:
                result['snapshot'] = json.loads(snapshot['snapshot'])
            report_snapshot = result['report'].get('snapshot') if isinstance(result['report'], dict) else None
            effective_snapshot = result.get('snapshot') or report_snapshot
            if isinstance(effective_snapshot, dict):
                scope = effective_snapshot.get('scope') or {}
                if scope:
                    result['scope'] = scope
                    result['period'] = scope.get('period')
                    result['base'] = scope.get('base')
            verdict = self._verdict(db, run_id)
            if verdict is not None:
                result['issuance'] = verdict
                result['publication'] = verdict
                verdict_scope = verdict.get('scope') if isinstance(verdict.get('scope'), dict) else None
                if verdict_scope:
                    result['scope'] = verdict_scope
                    result['period'] = verdict_scope.get('period')
                    result['base'] = verdict_scope.get('base')
                result['decision'] = verdict.get('decision', verdict.get('status'))
                result['conclusion'] = verdict.get('conclusion', verdict.get('decision'))
                result['conclusion_label'] = verdict.get('conclusion_label')
                result['reason_code'] = verdict.get('reason_code')
                result['reason'] = verdict.get('reason', verdict.get('reason_code'))
                result['reason_text'] = verdict.get('reason_text') or verdict.get('message')
                for key in ('can_publish', 'can_issue', 'allowed', 'safe', 'additional_hidden',
                            'additional_suppressed', 'supplement_count', 'additional_count',
                            'protected_fixed', 'fixed_variables'):
                    if key in verdict:
                        result[key] = verdict[key]
            bulletin = db.execute('SELECT payload FROM publication_ledger WHERE run_id=?', (run_id,)).fetchone()
            if bulletin is not None:
                bulletin_payload = json.loads(bulletin['payload'])
                result['bulletin'] = bulletin_payload
                if isinstance(result.get('publication'), dict):
                    result['publication'] = {
                        **result['publication'],
                        'bulletin_id': bulletin_payload.get('bulletin_id'),
                        'seq': bulletin_payload.get('seq'),
                        'published_at': bulletin_payload.get('created'),
                        'numbers': bulletin_payload.get('numbers'),
                        'bulletin_status': bulletin_payload.get('status'),
                    }
                else:
                    result['publication'] = bulletin_payload
        return result

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,created,status,revision,note FROM runs ORDER BY created DESC,id LIMIT 50').fetchall()
            items = [dict(row) for row in rows]
            for item in items:
                verdict = self._verdict(db, item['id'])
                if verdict:
                    item['decision'] = verdict.get('decision', verdict.get('status'))
                    item['reason_code'] = verdict.get('reason_code')
                    item['reason'] = verdict.get('reason')
        return items

    def revise(self, run_id, revision, status, note, now, verdict=None):
        with self.connect() as db:
            cursor = db.execute('UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 WHERE id=? AND revision=?',
                                (status, note, now, run_id, revision))
            if cursor.rowcount != 1:
                raise DomainIssue('STALE_REVIEW')
            if verdict is not None:
                db.execute('INSERT INTO review_verdicts(run_id,payload) VALUES (?,?) '
                           'ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload',
                           (run_id, json.dumps(verdict, ensure_ascii=False, sort_keys=True)))
        return self.load(run_id)

    def save_verdict(self, run_id, verdict):
        with self.connect() as db:
            db.execute('INSERT INTO review_verdicts(run_id,payload) VALUES (?,?) '
                       'ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload',
                       (run_id, json.dumps(verdict, ensure_ascii=False, sort_keys=True)))
        return self.load(run_id)

    def remember_request(self, idem_key, run_id, now):
        with self.connect() as db:
            existing = db.execute('SELECT run_id FROM idempotent_requests WHERE idem_key=?', (idem_key,)).fetchone()
            if existing is not None:
                if existing['run_id'] != run_id:
                    raise DomainIssue('IDEMPOTENCY_CONFLICT')
                return
            db.execute('INSERT INTO idempotent_requests(idem_key,run_id,created) VALUES (?,?,?)',
                       (idem_key, run_id, now))

    def find_idempotent_request(self, idem_key):
        with self.connect() as db:
            row = db.execute('SELECT run_id FROM idempotent_requests WHERE idem_key=?', (idem_key,)).fetchone()
            return row['run_id'] if row else None

    def add_publication(self, payload):
        with self.connect() as db:
            scope = payload.get('scope') or {}
            period = str(scope.get('period', 'default'))
            base_key = str(scope.get('base', 'default'))
            row = db.execute('SELECT COALESCE(MAX(seq),0)+1 AS next_seq FROM publication_ledger WHERE period=? AND base_key=?',
                             (period, base_key)).fetchone()
            payload['seq'] = row['next_seq']
            db.execute('INSERT INTO publication_ledger(run_id,bulletin_id,period,base_key,seq,created,payload) '
                       'VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING',
                       (payload['run_id'], payload['bulletin_id'], period, base_key, payload['seq'],
                        payload['created'], json.dumps(payload, ensure_ascii=False, sort_keys=True)))
        return self.load(payload['run_id'])

    def publications(self, period=None, base=None):
        query = 'SELECT payload FROM publication_ledger WHERE 1=1'
        values = []
        if period is not None:
            query += ' AND period=?'
            values.append(str(period))
        if base is not None:
            query += ' AND base_key=?'
            values.append(str(base))
        query += ' ORDER BY seq,created,run_id'
        with self.connect() as db:
            rows = db.execute(query, values).fetchall()
        return [json.loads(row['payload']) for row in rows]

    def bulletins(self, period=None, base=None):
        return self.publications(period, base)

    def list_publications(self, period=None, base=None):
        return self.publications(period, base)

    def list_bulletins(self, period=None, base=None):
        return self.publications(period, base)

    # Naming aliases used by different parts of the workbench vocabulary.
    def approved_publications(self, period=None, base=None):
        return self.publications(period, base)

    def ledger(self, period=None, base=None):
        return self.publications(period, base)


Store.issued = Store.publications
Store.approved_bulletins = Store.publications
