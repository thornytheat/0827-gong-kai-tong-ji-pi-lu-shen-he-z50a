from app.business import measured


def compute(data):
    cells, threshold, expected = data
    hidden = {key:'PRIMARY' for key,count in cells.items() if count < threshold}

    def bridges():
        exposed = []
        for a,b in hidden:
            seen = {('row',a)}
            queue = [('row',a)]
            while queue:
                axis,n = queue.pop()
                for left,right in hidden:
                    if (left,right)==(a,b):
                        continue
                    adjoining = ('col',right) if axis=='row' and left==n else (('row',left) if axis=='col' and right==n else None)
                    if adjoining and adjoining not in seen:
                        seen.add(adjoining)
                        queue.append(adjoining)
            if ('col',b) not in seen:
                exposed.append((a,b))
        return sorted(exposed)

    exposed = bridges()
    candidates = sorted(cells.keys()-hidden.keys(),key=lambda key:(cells[key],key))
    while exposed and candidates:
        endpoints_a = {a for a,b in exposed}
        endpoints_b = {b for a,b in exposed}
        index = next((i for i,(a,b) in enumerate(candidates) if a in endpoints_a or b in endpoints_b),0)
        hidden[candidates.pop(index)] = 'SECONDARY'
        exposed = bridges()
    return measured(len(hidden),expected,'格',{'suppressed':{f'{a}:{b}':reason for (a,b),reason in sorted(hidden.items())},'exposed':[f'{a}:{b}' for a,b in exposed]},bool(exposed))
