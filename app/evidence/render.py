import json


def artifact(run):
    bulletin = run.get('bulletin')
    if isinstance(bulletin, dict) and 'snapshot' in bulletin:
        bulletin = {key: value for key, value in bulletin.items() if key != 'snapshot'}
    packet = {
        'schema': 'review-evidence/1',
        'manifest': run['report']['manifest'],
        'balance': run['report']['balance'],
        'cases': run['report']['cases'],
        'intake': run['report']['intake'],
        'review': {key:run[key] for key in ('status','revision','note','reviewed') if key in run},
        'issuance': run.get('issuance'),
        'publication': run.get('publication'),
        'bulletin': bulletin,
    }
    packet['review'].update({key: run[key] for key in ('decision','conclusion','reason_code','reason') if key in run})
    return (json.dumps(packet, ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
