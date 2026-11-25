from flask import Blueprint, Response, current_app, jsonify, request

from app.business import DomainIssue
from app.evidence.render import artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.modules.publication import assess_publication
from app.review.service import execute, review, scope_from

api = Blueprint('api',__name__,url_prefix='/api')
STAGES = {'intake':1,'prepare':2,'calculate':3,'policy':4,'balance':5}


@api.post('/stages/<stage>')
def preview(stage):
    if stage not in STAGES:
        raise DomainIssue('STAGE_UNKNOWN')
    return jsonify(run(read_input(),STAGES[stage]))


@api.route('/runs',methods=['GET','POST'])
def runs():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(items=store.list())
    options = request.form.to_dict(flat=True)
    return jsonify(execute(store,read_input(),options))


@api.route('/publications', methods=['GET'])
@api.route('/bulletins', methods=['GET'])
def publications():
    store = current_app.extensions['runs']
    period = request.args.get('period')
    base = request.args.get('base')
    return jsonify(items=store.publications(period, base))


@api.route('/runs/<run_id>', methods=['GET','PATCH'])
def batch(run_id):
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(store.load(run_id))
    return jsonify(review(store,run_id,request.get_json(silent=True),request.headers))


@api.post('/runs/<run_id>/publish')
@api.post('/runs/<run_id>/issue')
def publish(run_id):
    store = current_app.extensions['runs']
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    body.setdefault('status', 'APPROVED')
    body.setdefault('revision', store.load(run_id)['revision'])
    body.setdefault('note', '提交签发并进行跨批反推复核')
    return jsonify(review(store, run_id, body, request.headers))


@api.route('/runs/<run_id>/publication', methods=['GET', 'POST'])
@api.route('/runs/<run_id>/issuance', methods=['GET', 'POST'])
@api.post('/runs/<run_id>/assess')
def assess_run(run_id):
    store = current_app.extensions['runs']
    current = store.load(run_id)
    if current.get('issuance') and request.method == 'GET':
        return jsonify(current['issuance'])
    body = request.get_json(silent=True) if request.method == 'POST' else None
    values = body if isinstance(body, dict) else request.args.to_dict(flat=True)
    snapshot = current.get('snapshot') or current['report'].get('snapshot')
    scope = scope_from(values, snapshot)
    history = [item for item in store.publications(scope['period'], scope['base']) if item.get('run_id') != run_id]
    verdict = assess_publication(snapshot, history)
    verdict['scope'] = scope
    return jsonify(verdict)


@api.post('/publication/check')
@api.post('/publications/check')
def check_publication_payload():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise DomainIssue('PUBLICATION_INPUT')
    current = body.get('current', body.get('snapshot') or body.get('tables'))
    prior = body.get('history', body.get('published', body.get('publications')))
    return jsonify(assess_publication(current, prior))


@api.get('/runs/<run_id>/evidence')
def export(run_id):
    report = current_app.extensions['runs'].load(run_id)
    return Response(artifact(report),mimetype='application/json', headers={
        'Content-Disposition':f'attachment; filename="review-{report["id"][:12]}.json"',
        'Cache-Control':'no-store',
    })
