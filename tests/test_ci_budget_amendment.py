"""Offline append-only authorization transitions; never invokes a supplier."""
from copy import deepcopy
import io
import json
import sqlite3
import zipfile

import pytest

from backend.core.reliability import ServiceError
from scripts.bounded_evaluation import Budget
from scripts.ci_budget_ledger import (FILES, REPOSITORY, WORKFLOW_ID, WORKFLOW_PATH,
    authorization_contract, contract, digest, encoded, merge_snapshots, open_attempt, seal, validate_snapshot)
from scripts.reconcile_ci_budget import ROOT, audit_amendment_prerequisites


def owner(number=51000000000):
    return {'repository':REPOSITORY,'workflow_id':WORKFLOW_ID,'workflow_path':WORKFLOW_PATH,
            'run_id':number,'run_attempt':1,'head_sha':'a'*40}


def run(value):
    return {'id':value['run_id'],'run_attempt':value['run_attempt'],'head_sha':value['head_sha'],
            'repository':{'full_name':REPOSITORY},'workflow_id':WORKFLOW_ID,'path':WORKFLOW_PATH,
            'status':'completed'}


def packaged(folder,agreement,value):
    seal(folder/'provider-budget.sqlite3',agreement,value,folder)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(FILES):
            archive.write(folder/name,name)
    blob=stream.getvalue()
    artifact={'id':value['run_id'],'name':f"ci-provider-ledger-{value['run_id']}-1",'expired':False,
              'digest':'sha256:'+digest(blob),'workflow_run':{'id':value['run_id'],'head_sha':value['head_sha']}}
    return validate_snapshot(blob,artifact,run(value),1,agreement),blob,artifact


@pytest.fixture(scope='module')
def original():
    agreement=contract(ROOT)
    return authorization_contract(agreement,agreement['root_authorization_sha256'])


@pytest.fixture(scope='module')
def prior(tmp_path_factory,original):
    folder=tmp_path_factory.mktemp('original-budget')
    ledger=folder/'provider-budget.sqlite3'
    open_attempt(ledger,original,owner())
    budget=Budget(ledger,original['policy'],'answers')
    for _ in range(426):
        index=budget.reserve('llm',{'model':'offline','max_tokens':1})
        budget.settle(index,200,{'prompt_tokens':1,'completion_tokens':1})
    return packaged(folder,original,owner())


def synthetic_amendment(prior):
    """Use synthetic request contents with the real counts/caps, without shipping private ledgers."""
    snapshot=prior[0]
    agreement=deepcopy(contract(ROOT))
    amendment=agreement['amendment']
    amendment['preserved_requests_sha256']=digest(encoded(snapshot['rows']))
    amendment['preserved_origins_sha256']=digest(encoded(snapshot['origins']))
    sha=digest(encoded(amendment))
    version={'authorization':amendment,'authorization_sha256':sha,
             'policy':agreement['policy'],'policy_sha256':agreement['policy_sha256']}
    root=agreement['root_authorization_sha256']
    agreement.update(version,authorization_versions={root:agreement['authorization_versions'][root],sha:version})
    return agreement


def test_current_amendment_keeps_root_authorization_baseline_and_other_limits(original):
    amended=contract(ROOT)
    assert original['policy']['model_http']==1358 and amended['policy']['model_http']==1565
    assert amended['baseline_requests']==original['baseline_requests']
    assert amended['baseline_origins']==original['baseline_origins']
    assert amended['baseline_sha256']=='bbb75d082efa3f22355cfbb3556f766240ec234dc7e7733751ad0ffb581b0120'
    assert amended['root_authorization_sha256']=='29c6e5f0deed51f2002be94f6e271c118bba81690034a991d4203b6967d493af'
    assert {k:v for k,v in original['policy'].items() if k not in {'model_http','llm_http','total_http'}}=={
        k:v for k,v in amended['policy'].items() if k not in {'model_http','llm_http','total_http'}}
    assert amended['amendment']['additional_authorized_model_http']==207


def test_old_artifact_is_validated_using_its_original_cap_not_the_new_policy(prior):
    agreement=synthetic_amendment(prior)
    snapshot=validate_snapshot(prior[1],prior[2],run(owner()),1,agreement)
    assert snapshot['receipt']['authorization_sha256']==agreement['root_authorization_sha256']
    assert json.loads(zipfile.ZipFile(io.BytesIO(prior[1])).read('provider-budget.json'))['policy']['model_http']==1358


def test_amendment_restores_all_1269_rows_and_caps_all_future_attempts_at_296(tmp_path,prior):
    agreement=synthetic_amendment(prior)
    path=tmp_path/'new.sqlite3'
    remaining=open_attempt(path,agreement,owner(51000000001),prior[0])
    budget=Budget(path,agreement['policy'],'answers')
    report=budget.report()
    assert remaining['cumulative_requests']==1269 and remaining['remaining_model_http']==296
    assert report['requests']==prior[0]['rows']
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        assert [dict(row) for row in db.execute('SELECT * FROM ci_request_origins ORDER BY request_id')]==prior[0]['origins']
    assert report['policy_changes'][0]['next_lifetime_model_http_cap']==1358
    assert report['policy_changes'][1]['additional_authorized_model_http']==207
    for _ in range(296):
        index=budget.reserve('llm',{'model':'offline','max_tokens':1})
        budget.settle(index,200,{'prompt_tokens':1,'completion_tokens':1})
    assert index==1565
    with pytest.raises(ServiceError,match='exhausted'):
        budget.reserve('llm',{})
    folder=tmp_path/'sealed'
    folder.mkdir()
    (folder/'provider-budget.sqlite3').write_bytes(path.read_bytes())
    newer,_,_=packaged(folder,agreement,owner(51000000001))
    merged=merge_snapshots([newer,prior[0]],agreement)
    next_remaining=open_attempt(tmp_path/'rerun.sqlite3',agreement,owner(51000000002),merged)
    assert next_remaining['remaining_model_http']==0 and next_remaining['cumulative_requests']==1565


@pytest.mark.parametrize('change',['missing','short','requests','origins'])
def test_missing_or_changed_history_cannot_activate_the_amendment(tmp_path,prior,change):
    agreement=synthetic_amendment(prior)
    snapshot=deepcopy(prior[0])
    if change=='missing':
        snapshot=None
    elif change=='short':
        snapshot['rows']=snapshot['rows'][:-1]
    elif change=='requests':
        snapshot['rows'][-1]['status']=500
    else:
        snapshot['origins'][-1]['head_sha']='f'*40
    path=tmp_path/'new.sqlite3'
    with pytest.raises(ValueError,match='verified existing ledger|1269-request'):
        open_attempt(path,agreement,owner(51000000001),snapshot)
    assert not path.exists()


@pytest.mark.parametrize('halt,missing',[(True,False),(False,True)])
def test_amendment_preserves_halt_and_conservative_unknown_usage(tmp_path,prior,original,halt,missing):
    folder=tmp_path/'modified-original'
    folder.mkdir()
    path=folder/'provider-budget.sqlite3'
    path.write_bytes(prior[0]['sqlite'])
    with sqlite3.connect(path) as db:
        if halt:
            db.execute("INSERT INTO state VALUES('halt','http_429')")
            db.execute('UPDATE requests SET status=429 WHERE id=1269')
        if missing:
            db.execute('UPDATE requests SET prompt_tokens=NULL,completion_tokens=NULL WHERE id=1269')
    changed=packaged(folder,original,owner())
    agreement=synthetic_amendment(changed)
    target=tmp_path/'new.sqlite3'
    remaining=open_attempt(target,agreement,owner(51000000001),changed[0])
    budget=Budget(target,agreement['policy'])
    assert budget.report()['requests']==changed[0]['rows']
    assert remaining['known_requests_with_missing_usage']==int(missing)
    assert budget.report()['charged_input_tokens']==json.loads(zipfile.ZipFile(io.BytesIO(changed[1])).read('provider-budget.json'))['charged_input_tokens']
    if halt:
        assert remaining['halt']=='http_429'
        with pytest.raises(ServiceError,match='stopped'):
            budget.reserve('llm',{})


@pytest.mark.parametrize('field',['status','conclusion','head_sha','id','repository','path'])
def test_conditional_amendment_waits_for_the_two_exact_required_workflows(field):
    agreement=contract(ROOT)
    condition=agreement['amendment']['activation_prerequisites']
    class API:
        def json(self,path):
            number=int(path.rsplit('/',1)[-1])
            value={'id':number,'repository':{'full_name':REPOSITORY},'head_sha':condition['head_sha'],
                   'path':'.github/workflows/ci.yml' if number==condition['runs']['CI'] else '.github/workflows/security.yml',
                   'status':'completed','conclusion':'success'}
            value[field]={'full_name':'other/repository'} if field=='repository' else ('b'*40 if field=='head_sha' else 'wrong')
            return value
    with pytest.raises(ValueError,match='explicitly required exact-source'):
        audit_amendment_prerequisites(API(),agreement)


def test_amended_receipt_cannot_be_treated_as_an_original_authorization(tmp_path,prior,original):
    agreement=synthetic_amendment(prior)
    folder=tmp_path/'new'
    folder.mkdir()
    open_attempt(folder/'provider-budget.sqlite3',agreement,owner(51000000001),prior[0])
    newer,blob,artifact=packaged(folder,agreement,owner(51000000001))
    assert newer['rows']==prior[0]['rows']
    assert merge_snapshots([newer,prior[0]],agreement)['receipt']['authorization_sha256']==agreement['authorization_sha256']
    with pytest.raises(ValueError,match='unknown cumulative authorization'):
        validate_snapshot(blob,artifact,run(owner(51000000001)),1,original)


def test_restored_old_runtime_binding_is_historical_and_unbound_zero_spend_can_be_restored(tmp_path,prior,original):
    folder=tmp_path/'previous-runtime'
    folder.mkdir()
    path=folder/'provider-budget.sqlite3'
    path.write_bytes(prior[0]['sqlite'])
    historical_key=f"ci_source_context_{owner()['run_id']}_1"
    with sqlite3.connect(path) as db:
        db.execute("INSERT OR REPLACE INTO state VALUES('ci_source_context_sha256',?)",('c'*64,))
        db.execute('INSERT INTO state VALUES(?,?)',(historical_key,'c'*64))
    bound=packaged(folder,original,owner())
    agreement=synthetic_amendment(bound)
    new_folder=tmp_path/'accounting-only'
    new_folder.mkdir()
    remaining=open_attempt(new_folder/'provider-budget.sqlite3',agreement,owner(51000000001),bound[0])
    assert remaining['remaining_model_http']==296
    unbound,_,_=packaged(new_folder,agreement,owner(51000000001))
    assert unbound['receipt']['source_context_sha256'] is None
    assert unbound['receipt']['new_requests']==0
    assert unbound['states'][historical_key]=='c'*64
    assert unbound['states']['ci_source_context_sha256'] is None
    assert json.loads(unbound['states']['ci_inherited_source_context_51000000001_1'])['run']==owner()
    next_remaining=open_attempt(tmp_path/'next.sqlite3',agreement,owner(51000000002),unbound)
    assert next_remaining['remaining_model_http']==296 and next_remaining['cumulative_requests']==1269


def test_accounting_only_seals_zero_spend_and_cannot_start_without_real_preflight(tmp_path,original,monkeypatch):
    from scripts import reconcile_ci_budget as cli
    from scripts import ci_source_context
    value=owner()
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr(cli,'contract',lambda _:original)
    monkeypatch.setattr(cli,'identity',lambda _:value)
    monkeypatch.setattr(cli.subprocess,'check_output',lambda *args,**kwargs:value['head_sha'])
    class API:
        def run(self,*_):
            return run(value)
    monkeypatch.setattr(cli,'GitHub',lambda _:API())
    def reconcile_without_network(api,current,agreement,path,sha):
        remaining=open_attempt(path,agreement,current)
        return {'run':current,'authorization_sha256':agreement['authorization_sha256'],
                'remaining':remaining,'supplier_http':0}
    monkeypatch.setattr(cli,'reconcile',reconcile_without_network)
    def forbidden(*_,**__):
        raise AssertionError('Accounting-only attempted interpreter or source binding')
    monkeypatch.setattr(ci_source_context,'bind_source',forbidden)
    out=tmp_path/'.cache/eval'
    ledger=out/'provider-budget.sqlite3'
    args=['--ledger',str(ledger),'--output-dir',str(out)]
    assert cli.main(['reconcile','--accounting-only',*args])==0
    before=Budget(ledger,original['policy']).report()['requests']
    assert len(before)==843
    assert 'source_context' not in json.loads((out/'ci-reconciliation.json').read_bytes())
    assert cli.main(['seal',*args])==0
    receipt=json.loads((out/'ci-ledger-receipt.json').read_bytes())
    assert receipt['new_requests']==0 and receipt['cumulative_requests']==843
    assert cli.main(['start',*args])==1  # No actual interpreter/source receipt exists.
    assert Budget(ledger,original['policy']).report()['requests']==before
    assert cli.main(['bind','--accounting-only',*args])==1


def test_delayed_binding_requires_the_same_accounting_run_and_authorization(tmp_path,original,monkeypatch):
    from scripts import reconcile_ci_budget as cli
    from scripts import ci_source_context
    value=owner()
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr(cli,'contract',lambda _:original)
    monkeypatch.setattr(cli,'identity',lambda _:value)
    monkeypatch.setattr(cli.subprocess,'check_output',lambda *args,**kwargs:value['head_sha'])
    class API:
        def run(self,*_):
            return run(value)
    monkeypatch.setattr(cli,'GitHub',lambda _:API())
    out=tmp_path/'.cache/eval'
    out.mkdir(parents=True)
    ledger=out/'provider-budget.sqlite3'
    open_attempt(ledger,original,value)
    reconciliation={'run':value,'authorization_sha256':original['authorization_sha256']}
    (out/'ci-reconciliation.json').write_text(json.dumps(reconciliation),encoding='utf-8')
    calls=[]
    monkeypatch.setattr(ci_source_context,'bind_source',lambda root,budget,current:(calls.append(current) or {'verified':'fixture'}))
    args=['bind','--ledger',str(ledger),'--output-dir',str(out)]
    assert cli.main(args)==0 and calls==[value]
    assert json.loads((out/'ci-reconciliation.json').read_bytes())['source_context']=={'verified':'fixture'}
    reconciliation['run']=owner(value['run_id']+1)
    (out/'ci-reconciliation.json').write_text(json.dumps(reconciliation),encoding='utf-8')
    assert cli.main(args)==1 and calls==[value]
