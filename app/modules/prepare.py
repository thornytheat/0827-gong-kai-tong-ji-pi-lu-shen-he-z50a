from app.business import DomainIssue, by_kind, prepared, single


def prepare(rows):
    release = single(rows,'release')
    source = by_kind(rows,'cell')
    cells = {(r['district'],r['category']):r['count'] for r in source}
    if len(cells) != len(source):
        raise DomainIssue('CELL_CONFLICT')
    districts = {a for a,b in cells}
    categories = {b for a,b in cells}
    if not 0 < len(cells) <= 150 or len(cells) != len(districts)*len(categories) or not release['threshold']:
        raise DomainIssue('INVALID_TABLE')
    return prepared(len(cells),{'districts':len(districts),'categories':len(categories),'threshold':release['threshold']},(cells,release['threshold'],release['expected_hidden']))
