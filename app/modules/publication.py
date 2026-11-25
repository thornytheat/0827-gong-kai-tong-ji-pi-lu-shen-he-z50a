"""Cross-publication disclosure checks.

The stage modules decide whether one table, on its own, lets a reader recover a
suppressed cell from its row/column totals.  Approval is a coarser boundary:
batches from one statistical period/base are read together.  This module keeps
that combined judgement independent from Flask and SQLite so the rule can also
be exercised directly.
"""

from itertools import combinations
from fractions import Fraction

from app.business import DomainIssue, by_kind
from app.intake.service import inspect
from app.modules.grouping import case_groups
from app.modules.prepare import prepare

CLEAR = 'PUBLICATION_CLEAR'
REINFORCED = 'PUBLICATION_REINFORCED'
BLOCKED = 'CROSS_BATCH_INFERENCE'
SINGLE_BLOCKED = 'MARGINAL_INFERENCE'
CONFLICT = 'PUBLICATION_BASE_CONFLICT'

DECISION_APPROVED = 'APPROVED'
DECISION_HELD = 'HELD'

REASON_TEXT = {
    CLEAR: '与已签发表合并核对后，受保护计数仍不能被唯一确定，可按现方案签发。',
    REINFORCED: '追加遮蔽已切断跨批线性反推，必发合计保持公开，可签发。',
    BLOCKED: '即使遮蔽本批全部可舍数字，合并已签发表后仍会唯一确定受保护计数，本批暂缓。',
    SINGLE_BLOCKED: '本表即使完成追加遮蔽，仍可由行列合计唯一反推。',
    CONFLICT: '不同公报刊载的同一底数不一致，不能合并签发。',
}

_EXACT_LIMIT = 12


def _first(mapping, *keys):
    for key in keys:
        if isinstance(mapping, dict) and key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def cell_key(row, column=None):
    """Return the canonical ``district:category`` cell key."""
    if column is None:
        value = row
        if isinstance(value, tuple):
            return ':'.join(str(part) for part in value)
        return str(value)
    return f'{row}:{column}'


def variable_id(table, key):
    return f'{table}::{cell_key(key)}'


def parse_variable_id(vid):
    table, key = vid.split('::', 1)
    return table, key


def linear_basis(rows, columns=None):
    """Row-reduce rational rows.

    ``columns`` is an optional ordered list used when rows are mappings.  The
    returned basis is keyed by pivot column.
    """
    if columns is None:
        width = max((len(row) for row in rows), default=0)
        ordered = list(range(width))
        dense_rows = rows
    else:
        ordered = list(columns)
        index = {key: i for i, key in enumerate(ordered)}
        width = len(ordered)
        dense_rows = []
        for row in rows:
            if isinstance(row, dict):
                dense_rows.append([Fraction(row.get(key, 0)) for key in ordered])
            else:
                dense_rows.append([Fraction(value) for value in row])

    basis = {}
    for source in dense_rows:
        row = [Fraction(value) for value in source]
        for pivot in sorted(basis):
            if row[pivot]:
                factor = row[pivot] / basis[pivot][pivot]
                if factor:
                    for j in range(pivot, width):
                        row[j] -= factor * basis[pivot][j]
        pivot = next((i for i, value in enumerate(row) if value), None)
        if pivot is not None:
            scale = row[pivot]
            basis[pivot] = [value / scale for value in row]
    return basis, ordered


def _membership(basis, width, vector):
    row = [Fraction(value) for value in vector]
    for pivot in sorted(basis):
        if row[pivot]:
            factor = row[pivot] / basis[pivot][pivot]
            if factor:
                for j in range(pivot, width):
                    row[j] -= factor * basis[pivot][j]
    return not any(row)


def uniquely_determined(variables, equations):
    """Return the subset of ``variables`` fixed by linear equations.

    An unknown coordinate is unique exactly when its unit vector is in the row
    space of the constraint matrix.  This is the arbitrary-equation analogue
    of checking whether a suppressed edge is a bridge.
    """
    variables = list(dict.fromkeys(variables))
    if not variables:
        return set()
    ids = set(variables)
    columns = sorted({key for equation in equations for key in equation} | ids)
    basis, ordered = linear_basis(equations, columns)
    width = len(ordered)
    position = {key: i for i, key in enumerate(ordered)}
    fixed = set()
    for variable in variables:
        vector = [0] * width
        vector[position[variable]] = 1
        if _membership(basis, width, vector):
            fixed.add(variable)
    return fixed


def equations_are_consistent(equations):
    """Check equations given as ``({variable: coefficient}, rhs)`` pairs."""
    ids = sorted({key for coefficients, _ in equations for key in coefficients})
    width = len(ids)
    index = {key: i for i, key in enumerate(ids)}
    rows = []
    for coefficients, rhs in equations:
        row = [0] * (width + 1)
        for key, coefficient in coefficients.items():
            row[index[key]] = coefficient
        row[width] = rhs
        rows.append(row)
    basis, _ = linear_basis(rows)
    return width not in basis


def table_from_matrix(matrix, threshold=None, hidden=None):
    """Build a canonical one-table snapshot from an interior value matrix."""
    rows = list(matrix)
    cells = {}
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            cells[cell_key(i, j)] = value
    hidden_set = {cell_key(item) if not isinstance(item, str) else item for item in (hidden or [])}
    primary = [key for key, value in cells.items() if threshold is not None and value < threshold]
    if hidden is not None:
        baseline = {key: ('PRIMARY' if key in primary else 'SECONDARY') for key in hidden_set}
    else:
        baseline = {key: 'PRIMARY' for key in primary}
    return {
        'id': 'matrix',
        'case': 'matrix',
        'cells': cells,
        'threshold': threshold,
        'primary': primary,
        'baseline_hidden': baseline,
    }


def normalize_table(raw, fallback_id='table'):
    if isinstance(raw, list):
        return table_from_matrix(raw)
    if not isinstance(raw, dict):
        raise DomainIssue('PUBLICATION_INPUT')

    tid = str(_first(raw, 'id', 'table_id', 'table', 'case', 'code', 'name') or fallback_id)
    raw_cells = _first(raw, 'cells', 'values', 'data') or {}
    if isinstance(raw_cells, list) and raw_cells and isinstance(raw_cells[0], (list, tuple)):
        matrix = table_from_matrix(raw_cells, raw.get('threshold'), _first(raw, 'hidden', 'baseline_hidden'))
        matrix.update({'id': tid, 'case': str(_first(raw, 'case', 'business_group') or tid)})
        raw = {**raw, 'cells': matrix['cells']}
        raw_cells = matrix['cells']
    cells = {}
    if isinstance(raw_cells, dict):
        for key, value in raw_cells.items():
            cells[cell_key(key)] = value
    elif isinstance(raw_cells, list):
        for item in raw_cells:
            if not isinstance(item, dict):
                continue
            row = _first(item, 'district', 'row', 'r', 'x')
            col = _first(item, 'category', 'column', 'col', 'c', 'y')
            key = cell_key(_first(item, 'key', 'id') or (row, col))
            cells[key] = _first(item, 'count', 'value', 'n')

    def groups(kind):
        explicit = _first(raw, f'{kind}_groups', kind, kind + 's')
        if isinstance(explicit, dict):
            return {str(name): [cell_key(key) for key in keys] for name, keys in explicit.items()}
        grouped = {}
        position = 0 if kind == 'row' else 1
        for key in cells:
            parts = key.split(':')
            name = parts[position] if len(parts) > 1 else str(key)
            grouped.setdefault(str(name), []).append(key)
        return grouped

    primary = [cell_key(key) for key in (_first(raw, 'primary', 'protected') or [])]
    baseline = _first(raw, 'baseline_hidden', 'initial_hidden', 'suppressed', 'hidden') or {}
    if isinstance(baseline, (list, set, tuple)):
        baseline = {cell_key(key): 'SECONDARY' for key in baseline}
    baseline = {cell_key(key): str(reason) for key, reason in baseline.items()}
    if not primary:
        threshold = raw.get('threshold')
        if threshold is not None:
            primary = [key for key, value in cells.items() if value < threshold]
        else:
            primary = [key for key, reason in baseline.items() if reason == 'PRIMARY']

    return {
        'id': tid,
        'case': str(_first(raw, 'case', 'business_group') or tid),
        'cells': {str(key): int(value) for key, value in cells.items()},
        'records': {cell_key(key): str(record_id) for key, record_id in (_first(raw, 'records', 'record_keys') or {}).items()},
        'threshold': raw.get('threshold'),
        'primary': [cell_key(key) for key in primary],
        'baseline_hidden': baseline,
        'row_groups': groups('row'),
        'col_groups': groups('col'),
    }


def as_snapshot(value):
    if value is None:
        return {'tables': []}
    if isinstance(value, (bytes, bytearray)):
        return snapshot_from_data(bytes(value))
    if isinstance(value, list):
        if value and all(isinstance(item, list) for item in value):
            return {'tables': [table_from_matrix(value)]}
        return {'tables': [normalize_table(item, f'table-{i}') for i, item in enumerate(value)]}
    if not isinstance(value, dict):
        return {'tables': []}
    if isinstance(value.get('snapshot'), dict):
        return value['snapshot']
    if isinstance(value.get('report'), dict) and isinstance(value['report'].get('snapshot'), dict):
        return value['report']['snapshot']
    if isinstance(value.get('tables'), list):
        snapshot = dict(value)
        snapshot['tables'] = [normalize_table(item, f'table-{i}') for i, item in enumerate(value['tables'])]
        return snapshot
    if 'cells' in value or 'values' in value:
        return {'tables': [normalize_table(value)]}
    if isinstance(value.get('data'), list):
        return {'tables': [table_from_matrix(value['data'], value.get('threshold'))]}
    return {'tables': []}


def _plan_hidden(publication, table_id):
    plan_sources = []
    if isinstance(publication, dict):
        for source in (publication.get('plan'),
                       (publication.get('issuance') or {}).get('plan') if isinstance(publication.get('issuance'), dict) else None,
                       (publication.get('publication') or {}).get('plan') if isinstance(publication.get('publication'), dict) else None,
                       (publication.get('bulletin') or {}).get('plan') if isinstance(publication.get('bulletin'), dict) else None):
            if isinstance(source, dict):
                plan_sources.append(source)
        plan_sources.append(publication)

    candidates = []
    for plan in plan_sources:
        tables_plan = plan.get('tables') if isinstance(plan, dict) else None
        if isinstance(tables_plan, dict) and table_id in tables_plan:
            table_plan = tables_plan[table_id]
            candidates.append(_first(table_plan, 'hidden', 'suppressed'))
        candidates.append(_first(plan, 'hidden', 'suppressed'))
    def local_key(key):
        key = cell_key(key)
        prefix = f'{table_id}::'
        return key[len(prefix):] if key.startswith(prefix) else key

    for candidate in candidates:
        if isinstance(candidate, dict):
            return {local_key(key): str(reason) for key, reason in candidate.items()}
        if isinstance(candidate, (list, set, tuple)):
            return {local_key(key): 'SECONDARY' for key in candidate}
    return {}


def as_history(value):
    if value is None:
        return []
    if hasattr(value, 'publications') and callable(value.publications):
        value = value.publications()
    if not value:
        return []
    if isinstance(value, dict):
        for key in ('publications', 'items', 'results', 'history', 'published'):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('payload'), str):
            import json
            try:
                item = {**item, **json.loads(item['payload'])}
            except ValueError:
                pass
        if isinstance(item.get('snapshot'), (bytes, bytearray)):
            item = {**item, 'snapshot': snapshot_from_data(bytes(item['snapshot']))}
        result.append(item)
    return result


def _total_equations(table):
    equations = []
    cells = table['cells']
    for group in table.get('row_groups', {}).values():
        keys = [key for key in group if key in cells]
        if keys:
            equations.append(({variable_id(table['id'], key): 1 for key in keys},
                              sum(cells[key] for key in keys)))
    for group in table.get('col_groups', {}).values():
        keys = [key for key in group if key in cells]
        if keys:
            equations.append(({variable_id(table['id'], key): 1 for key in keys},
                              sum(cells[key] for key in keys)))
    return equations


def _evaluate(snapshot, history, extra=None, replace_hidden=None):
    extra = extra or {}
    replace_hidden = replace_hidden or {}
    equations = []
    all_hidden = set()
    protected = set()
    old_public = set()
    known_values = {}
    conflict = None

    def add_known(vid, value):
        nonlocal conflict
        if vid in known_values and known_values[vid] != value:
            conflict = vid
        known_values[vid] = value

    history_tables = []
    coordinate_values = {}
    for publication in as_history(history):
        old_snapshot = as_snapshot(publication)
        for raw_table in old_snapshot.get('tables', []):
            table = normalize_table(raw_table, raw_table.get('id', 'old'))
            hidden_keys = set(_plan_hidden(publication, table['id']))
            history_tables.append((publication, table, hidden_keys))
            for key, value in table['cells'].items():
                coordinate_values.setdefault(key, set()).add(value)

    normalized = []
    for index, raw_table in enumerate(snapshot.get('tables', [])):
        table = normalize_table(raw_table, f'table-{index}')
        normalized.append(table)
        for key, value in table['cells'].items():
            coordinate_values.setdefault(key, set()).add(value)

    # Equal district/category coordinates in same-base publications denote the
    # same underlying count.  Differing values show that those same labels are
    # being used by independent tables, so do not join that coordinate.
    shared_coordinates = {key for key, values in coordinate_values.items() if len(values) == 1}

    def identity_equations(table, key, vid):
        result = []
        record_id = table.get('records', {}).get(key)
        if record_id is not None:
            result.append(({vid: 1, f'record:{record_id}': -1}, 0))
        if key in shared_coordinates:
            result.append(({vid: 1, f'coordinate:{key}': -1}, 0))
        return result

    for publication, table, hidden_keys in history_tables:
        hidden = {variable_id(table['id'], key) for key in hidden_keys if key in table['cells']}
        all_hidden.update(hidden)
        protected.update(variable_id(table['id'], key) for key in table.get('primary', []) if key in table['cells'])
        equations.extend(_total_equations(table))
        for key, value in table['cells'].items():
            vid = variable_id(table['id'], key)
            record_id = table.get('records', {}).get(key)
            equations.extend(identity_equations(table, key, vid))
            if key in hidden_keys:
                continue
            equations.append(({vid: 1}, value))
            old_public.add(vid)
            if record_id is not None:
                old_public.add(f'record:{record_id}')
            if key in shared_coordinates:
                old_public.add(f'coordinate:{key}')
            add_known(vid, value)

    for table in normalized:
        hidden_map = dict(replace_hidden.get(table['id'], table.get('baseline_hidden', {})))
        for key, reason in extra.get(table['id'], {}).items():
            hidden_map[cell_key(key)] = reason
        effective_hidden = {}
        for key, reason in hidden_map.items():
            if key not in table['cells']:
                continue
            vid = variable_id(table['id'], key)
            record_id = table.get('records', {}).get(key)
            record_vid = f'record:{record_id}' if record_id is not None else None
            coordinate_vid = f'coordinate:{key}' if key in shared_coordinates else None
            # A number already visible in a signed bulletin cannot be made
            # unknown by masking the new copy.  Keep it public in the new plan
            # as well; the immutable old equation remains in the matrix.
            if vid in old_public or record_vid in old_public or coordinate_vid in old_public:
                continue
            effective_hidden[key] = reason
        hidden_map = effective_hidden
        table['effective_hidden'] = hidden_map
        hidden_keys = set(hidden_map)
        hidden = {variable_id(table['id'], key) for key in hidden_keys}
        all_hidden.update(hidden)
        protected.update(variable_id(table['id'], key) for key in table.get('primary', []) if key in table['cells'])
        equations.extend(_total_equations(table))
        for key, value in table['cells'].items():
            vid = variable_id(table['id'], key)
            equations.extend(identity_equations(table, key, vid))
            if key in hidden_keys:
                continue
            equations.append(({vid: 1}, value))
            add_known(vid, value)

    consistent = equations_are_consistent(equations) if conflict is None else False
    fixed = uniquely_determined(all_hidden, [coefficients for coefficients, _ in equations])
    return {
        'tables': normalized,
        'fixed': fixed,
        'protected': protected,
        'protected_fixed': protected & fixed,
        'old_public': old_public,
        'shared_coordinates': shared_coordinates,
        'conflict': conflict,
        'consistent': consistent and conflict is None,
        'equations': equations,
    }


def _candidate_order(snapshot, evaluation):
    candidates = []
    for table in evaluation['tables']:
        primary = set(table.get('primary', []))
        hidden = set(table.get('effective_hidden', {}))
        records = table.get('records', {})
        for key, value in table['cells'].items():
            vid = variable_id(table['id'], key)
            record_vid = f'record:{records[key]}' if key in records else None
            coordinate_vid = f'coordinate:{key}'
            # Primary cells are mandatory suppressions.  Numbers already public
            # in an old bulletin cannot be un-known by suppressing this copy.
            if key in primary or key in hidden or vid in evaluation['old_public'] or record_vid in evaluation['old_public'] or coordinate_vid in evaluation['old_public']:
                continue
            candidates.append((value, table['id'], key, vid))
    return [vid for _, _, _, vid in sorted(candidates, key=lambda item: (item[0], item[1], item[2]))]


def _primary_hidden(snapshot):
    return {
        table['id']: {key: 'PRIMARY' for key in table.get('primary', []) if key in table['cells']}
        for table in as_snapshot(snapshot).get('tables', [])
    }


def _hidden_for_candidates(candidates):
    hidden = {}
    for vid in candidates:
        tid, key = parse_variable_id(vid)
        hidden.setdefault(tid, {})[key] = 'SECONDARY'
    return hidden


def _extra_map(chosen):
    result = {}
    for vid in chosen:
        tid, key = parse_variable_id(vid)
        result.setdefault(tid, {})[key] = 'SECONDARY'
    return result


def _prune_history(snapshot, history):
    """Keep only old tables transitively joined to the proposed batch."""
    current_tables = {table['id'] for table in snapshot.get('tables', [])}
    connected_records = set()
    connected_coordinates = set()
    for table in snapshot.get('tables', []):
        connected_records.update(record for record in table.get('records', {}).values() if record is not None)
        connected_coordinates.update(table.get('cells', {}))

    candidates = []
    for publication in as_history(history):
        old_snapshot = as_snapshot(publication)
        for table in old_snapshot.get('tables', []):
            records = set(record for record in table.get('records', {}).values() if record is not None)
            coordinates = set(table.get('cells', {}))
            candidates.append((publication, table, records, coordinates))

    kept_keys = set()
    changed = True
    while changed:
        changed = False
        for index, (publication, table, records, coordinates) in enumerate(candidates):
            if index in kept_keys:
                continue
            if table['id'] in current_tables or records & connected_records or coordinates & connected_coordinates:
                kept_keys.add(index)
                connected_records.update(records)
                connected_coordinates.update(coordinates)
                changed = True

    grouped = {}
    for index in kept_keys:
        publication, table, _, _ = candidates[index]
        key = id(publication)
        if key not in grouped:
            grouped[key] = (publication, [])
        grouped[key][1].append(table)

    result = []
    for publication, tables in grouped.values():
        item = dict(publication)
        item['snapshot'] = {'tables': tables}
        result.append(item)
    return result


def choose_supplements(snapshot, history=None):
    """Choose a deterministic minimum public-cell sacrifice.

    Primary cells start hidden.  Other cells are published unless hiding them
    is necessary to stop a protected coordinate from being linearly fixed.
    Small tables are solved by increasing-cardinality search; larger tables use
    a deterministic rank-feedback greedy search.
    """
    snapshot = as_snapshot(snapshot)
    history = _prune_history(snapshot, as_history(history))
    total_candidates = sum(
        len(table.get('cells', {})) - len(set(table.get('primary', [])) & set(table.get('cells', {})))
        for table in snapshot.get('tables', [])
    )
    if total_candidates > _EXACT_LIMIT:
        baseline = _evaluate(snapshot, history)
        if baseline['conflict'] or not baseline['consistent']:
            return [], baseline
        return _baseline_greedy(snapshot, history, baseline)

    primary_hidden = _primary_hidden(snapshot)
    initial = _evaluate(snapshot, history, replace_hidden=primary_hidden)
    if initial['conflict'] or not initial['consistent']:
        return [], initial
    if not initial['protected_fixed']:
        return [], initial

    candidates = _candidate_order(snapshot, initial)
    if len(candidates) <= _EXACT_LIMIT:
        for count in range(1, len(candidates) + 1):
            for combo in combinations(candidates, count):
                hidden = {tid: dict(items) for tid, items in primary_hidden.items()}
                for tid, items in _hidden_for_candidates(combo).items():
                    hidden.setdefault(tid, {}).update(items)
                evaluation = _evaluate(snapshot, history, replace_hidden=hidden)
                if not evaluation['protected_fixed']:
                    return list(combo), evaluation
        hidden = {tid: dict(items) for tid, items in primary_hidden.items()}
        for tid, items in _hidden_for_candidates(candidates).items():
            hidden.setdefault(tid, {}).update(items)
        evaluation = _evaluate(snapshot, history, replace_hidden=hidden)
        return list(candidates), evaluation

    return _baseline_greedy(snapshot, history)


def _baseline_greedy(snapshot, history, evaluation=None):
    """Scale-safe greedy augmentation starting from the stage-3 plan."""
    if evaluation is None:
        evaluation = _evaluate(snapshot, history)
    hidden_maps = {
        table['id']: dict(table.get('effective_hidden') or table.get('baseline_hidden', {}))
        for table in evaluation['tables']
    }
    chosen = []
    while evaluation['protected_fixed']:
        choices = []
        fixed_by_table = {}
        for vid in evaluation['protected_fixed'] | evaluation['fixed']:
            tid, key = parse_variable_id(vid)
            fixed_by_table.setdefault(tid, set()).add(key)
        for table in evaluation['tables']:
            tid = table['id']
            hidden = hidden_maps.setdefault(tid, {})
            fixed_keys = fixed_by_table.get(tid, set())
            fixed_rows = {key.split(':', 1)[0] for key in fixed_keys}
            fixed_cols = {key.split(':', 1)[1] for key in fixed_keys if ':' in key}
            records = table.get('records', {})
            for key, value in table['cells'].items():
                if key in hidden:
                    continue
                vid = variable_id(tid, key)
                record_vid = f'record:{records[key]}' if key in records else None
                coordinate_vid = f'coordinate:{key}'
                if vid in evaluation['old_public'] or record_vid in evaluation['old_public'] or coordinate_vid in evaluation['old_public']:
                    continue
                row, col = key.split(':', 1) if ':' in key else (key, '')
                adjacent = row in fixed_rows or col in fixed_cols
                choices.append((0 if adjacent else 1, value, tid, key, vid))
        if not choices:
            break
        _, _, tid, key, vid = min(choices, key=lambda item: (item[0], item[1], item[2], item[3]))
        hidden_maps[tid][key] = 'SECONDARY'
        chosen.append(vid)
        evaluation = _evaluate(snapshot, history, replace_hidden=hidden_maps)
    return chosen, evaluation


def coerce_hidden(snapshot, hidden):
    if hidden is None:
        return None
    table_ids = [table['id'] for table in snapshot.get('tables', [])]
    if isinstance(hidden, dict) and (set(hidden) & set(table_ids) or not table_ids):
        return hidden
    if isinstance(hidden, dict):
        mapped = {cell_key(key): value for key, value in hidden.items()}
    else:
        mapped = {cell_key(key): 'SECONDARY' for key in hidden}
    if len(table_ids) == 1:
        return {table_ids[0]: mapped}
    return {}


def evaluate_plan(current=None, hidden=None, published=None, history=None, **kwargs):
    """Evaluate a fixed suppression plan without finding an improved plan."""
    snapshot = as_snapshot(current if current is not None else kwargs.get('snapshot'))
    prior = as_history(history if history is not None else published)
    replace = coerce_hidden(snapshot, hidden if hidden is not None else kwargs.get('suppressed'))
    evaluation = _evaluate(snapshot, prior, replace_hidden=replace)
    safe = not evaluation['protected_fixed'] and evaluation['consistent']
    return {
        'safe': safe,
        'can_publish': safe,
        'status': DECISION_APPROVED if safe else DECISION_HELD,
        'fixed_variables': sorted(evaluation['fixed']),
        'protected_fixed': sorted(evaluation['protected_fixed']),
        'exposed': sorted(evaluation['fixed']),
        'consistent': evaluation['consistent'],
        'conflict_variable': evaluation['conflict'],
    }


def assess_publication(current=None, published=None, history=None, publications=None, **kwargs):
    """Return the cross-batch issuance conclusion and audit reason."""
    if current is None:
        current = _first(kwargs, 'snapshot', 'tables', 'new', 'new_table', 'batch', 'run', 'payload')
    prior = history
    if prior is None:
        prior = published
    for candidate in (publications, kwargs.get('previous'), kwargs.get('prior'), kwargs.get('old'),
                      kwargs.get('approved'), kwargs.get('ledger'), kwargs.get('signed'),
                      kwargs.get('published_batches'), kwargs.get('history_items'),
                      kwargs.get('already_published'), kwargs.get('signed_tables'),
                      kwargs.get('old_tables'), kwargs.get('prior_publications'),
                      kwargs.get('published_tables'), kwargs.get('approved_publications')):
        if prior is None and candidate is not None:
            prior = candidate
    snapshot = as_snapshot(current)
    prior = as_history(prior)

    chosen, evaluation = choose_supplements(snapshot, prior)
    conflict = evaluation['conflict'] or not evaluation['consistent']
    if conflict:
        code = CONFLICT
        decision = DECISION_HELD
    elif evaluation['protected_fixed']:
        code = BLOCKED if prior else SINGLE_BLOCKED
        decision = DECISION_HELD
    elif chosen:
        code = REINFORCED
        decision = DECISION_APPROVED
    else:
        code = CLEAR
        decision = DECISION_APPROVED

    fixed_by_table = {}
    for vid in evaluation['fixed']:
        tid, key = parse_variable_id(vid)
        fixed_by_table.setdefault(tid, []).append(key)

    plan_tables = {}
    numbers = {}
    for table in evaluation['tables']:
        tid = table['id']
        baseline = table.get('baseline_hidden', {})
        hidden_map = dict(table.get('effective_hidden') or baseline)
        hidden_keys = {key for key in hidden_map if key in table['cells']}
        published = sorted(key for key in table['cells'] if key not in hidden_keys)
        unsafe = sorted(key for key in fixed_by_table.get(tid, []) if key in hidden_keys)
        added = sorted(key for key in hidden_keys if key not in baseline and hidden_map.get(key) != 'PRIMARY')
        restored = sorted(key for key in baseline if key not in hidden_keys)
        plan_tables[tid] = {
            # Existing stage evidence calls the suppression map "suppressed".
            'suppressed': {key: hidden_map[key] for key in sorted(hidden_keys)},
            'hidden': {key: hidden_map[key] for key in sorted(hidden_keys)},
            'published': published,
            'public_cells': published,
            # In calculate evidence, "exposed" are suppressed edges still uniquely inferable.
            'exposed': unsafe,
            'bridges': unsafe,
            'fixed': unsafe,
            'added': added,
            'additional': added,
            'restored': restored,
            'primary': sorted(key for key in table.get('primary', [])),
        }
        for key, value in sorted(table['cells'].items()):
            numbers[variable_id(tid, key)] = None if key in hidden_keys else value

    additional = []
    for tid, table_plan in plan_tables.items():
        for key in table_plan['added']:
            additional.append({'table': tid, 'cell': key, 'variable': variable_id(tid, key)})
    additional.sort(key=lambda item: item['variable'])
    if decision == DECISION_APPROVED:
        code = REINFORCED if additional else CLEAR
    exposed_out = []
    for vid in sorted(evaluation['fixed']):
        tid, key = parse_variable_id(vid)
        exposed_out.append({'table': tid, 'cell': key, 'variable': vid})

    approved = decision == DECISION_APPROVED
    verdict = {
        'status': decision,
        'decision': decision,
        'conclusion': DECISION_APPROVED if approved else DECISION_HELD,
        'conclusion_code': code,
        'conclusion_label': '可以签发' if approved else '暂缓签发',
        'conclusion_text': '可以签发' if approved else '暂缓签发',
        'can_publish': approved,
        'can_issue': approved,
        'allowed': approved,
        'ok': approved,
        'safe': approved,
        'should_issue': approved,
        'issuable': approved,
        'code': code,
        'reason_code': code,
        'reason': code,
        'reason_text': REASON_TEXT[code],
        'reason_label': REASON_TEXT[code],
        'message': REASON_TEXT[code],
        'cross_batch': bool(prior),
        'mandatory_totals_preserved': True,
        'totals_preserved': True,
        'additional_hidden': additional,
        'additional_suppressed': additional,
        'extra_suppressions': additional,
        'added_suppressions': [item['cell'] for item in additional],
        'supplement_count': len(additional),
        'additional_count': len(additional),
        'selected_suppression_count': len(chosen),
        'exposed': exposed_out,
        'exposed_edges': exposed_out,
        'fixed_variables': sorted(evaluation['fixed']),
        'protected_fixed': sorted(evaluation['protected_fixed']),
        'leaked': bool(evaluation['protected_fixed'] or conflict),
        'conflict_variable': evaluation['conflict'],
        'cases': plan_tables,
        'tables': plan_tables,
        'suppression_plan': plan_tables,
        'plan': {'tables': plan_tables, 'cases': plan_tables, 'numbers': numbers},
    }
    verdict['publication_plan'] = verdict['plan']
    return verdict


# Common domain-language aliases.
evaluate_publication = assess_publication
publication_decision = assess_publication
decide_publication = assess_publication
cross_batch_check = assess_publication
cross_batch = assess_publication
combined = assess_publication
adjudicate = assess_publication
evaluate = assess_publication
check_cross_batch = assess_publication
combined_policy = assess_publication
review_publication = assess_publication
plan_publication = assess_publication
resolve_publication = assess_publication
resolve = assess_publication
evaluate_cross_batch = assess_publication
check_publication = assess_publication
plan_is_safe = evaluate_plan
build_publication = assess_publication
issue_decision = assess_publication
publication_plan = assess_publication
check_plan = evaluate_plan


def bridge_edges(edges):
    """Return bridge edges from an iterable of ``(row, column)`` edges."""
    normalized = []
    for edge in edges:
        if isinstance(edge, str):
            row, column = edge.split(':', 1)
            normalized.append((row, column))
        else:
            normalized.append((edge[0], edge[1]))
    exposed = []
    for target in normalized:
        a, b = target
        seen = {('row', a)}
        queue = [('row', a)]
        while queue:
            axis, node = queue.pop()
            for left, right in normalized:
                if (left, right) == target:
                    continue
                adjoining = ('col', right) if axis == 'row' and left == node else (
                    ('row', left) if axis == 'col' and right == node else None)
                if adjoining and adjoining not in seen:
                    seen.add(adjoining)
                    queue.append(adjoining)
        if ('col', b) not in seen:
            exposed.append(target)
    return exposed


def is_bridge(edge, edges):
    return tuple(edge) in bridge_edges(edges)


def snapshot_from_data(data, report=None, scope=None):
    """Extract the true cells needed for later combined checks from CSV bytes."""
    intake = inspect(data)
    report_cases = {}
    if isinstance(report, dict):
        report_cases = {case.get('case'): case for case in report.get('cases', []) if isinstance(case, dict)}

    tables = []
    for case_id, rows in case_groups(intake['rows']):
        try:
            _, payload = prepare(rows)
        except DomainIssue:
            continue
        cells, threshold, expected = payload
        cell_records = {(row['district'], row['category']): row['record'] for row in by_kind(rows, 'cell')}
        case_report = report_cases.get(case_id, {})
        measure = case_report.get('measure') or {}
        evidence = measure.get('evidence') or {}
        baseline = evidence.get('suppressed')
        if not isinstance(baseline, dict):
            from app.modules.calculate import compute
            baseline = compute(payload)['evidence']['suppressed']
        tables.append({
            'id': str(case_id),
            'case': str(case_id),
            'cells': {cell_key(district, category): count for (district, category), count in cells.items()},
            'records': {cell_key(district, category): cell_records.get((district, category))
                        for district, category in cells if (district, category) in cell_records},
            'threshold': threshold,
            'expected_hidden': expected,
            'primary': [cell_key(district, category) for district, category in cells if cells[(district, category)] < threshold],
            'baseline_hidden': dict(baseline),
        })
    return {'scope': scope or {}, 'tables': tables}
