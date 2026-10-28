"""Offline GitHub artifact/attempt mocks; no workflow dispatch or supplier HTTP."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import zipfile

import pytest

from backend.core.reliability import ServiceError
from scripts.bounded_evaluation import Budget
from scripts.ci_budget_ledger import (FILES, REPOSITORY, WORKFLOW_ID, WORKFLOW_PATH, contract, digest,
    authorization_contract, merge_snapshots, open_attempt, seal, validate_snapshot)
from scripts.reconcile_ci_budget import ROOT, gate_prerequisites, reconcile


def current(number=50000000000,attempt=1,sha='a'*40):
    return {'repository':REPOSITORY,'workflow_id':WORKFLOW_ID,'workflow_path':WORKFLOW_PATH,
            'run_id':number,'run_attempt':attempt,'head_sha':sha}


def run_of(value,status='completed'):
    return {'id':value['run_id'],'run_attempt':value['run_attempt'],'head_sha':value['head_sha'],
            'repository':{'full_name':REPOSITORY},'workflow_id':WORKFLOW_ID,'path':WORKFLOW_PATH,'status':status}


@pytest.fixture
def agreement():
    agreement=contract(ROOT)
    return authorization_contract(agreement,agreement['root_authorization_sha256'])


def package(folder,agreement,owner,*,parent=None,model='fixture',known=True,status=200):
    folder.mkdir()
    ledger=folder/'provider-budget.sqlite3'
    open_attempt(ledger,agreement,owner,parent)
    budget=Budget(ledger,agreement['policy'],'answers')
    index=budget.reserve('llm',{'model':model,'max_tokens':8000})
    if known:
        budget.settle(index,status,{'prompt_tokens':7,'completion_tokens':3})
    seal(ledger,agreement,owner,folder)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(FILES):
            archive.write(folder/name,name)
    blob=stream.getvalue()
    metadata={'id':owner['run_id']+owner['run_attempt'],'name':f"ci-provider-ledger-{owner['run_id']}-{owner['run_attempt']}",
              'expired':False,'digest':'sha256:'+digest(blob),'workflow_run':{'id':owner['run_id'],'head_sha':owner['head_sha']}}
    return validate_snapshot(blob,metadata,run_of(owner),owner['run_attempt'],agreement),blob,metadata


def test_lifetime_1358_preserves_843_and_cannot_restart_515_in_another_checkout(tmp_path,agreement):
    ledger=tmp_path/'provider-budget.sqlite3'
    remaining=open_attempt(ledger,agreement,current())
    assert remaining['historical_requests']==843 and remaining['remaining_model_http']==515
    assert remaining['remaining_input_tokens']==9101235 and remaining['remaining_output_tokens']==379122
    budget=Budget(ledger,agreement['policy'],'answers')

    def reserve(_):
        try:
            return budget.reserve('llm',{'model':'offline','max_tokens':0})
        except ServiceError as error:
            assert error.code=='evaluation_budget'
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids=[value for value in pool.map(reserve,range(523)) if value is not None]
    assert len(ids)==515 and min(ids)==844 and max(ids)==1358
    assert budget.report()['http_requests']==1358
    with pytest.raises(ValueError,match='cannot reset'):
        Budget(tmp_path/'different-run-number.sqlite3',agreement['policy'])


def test_failed_attempt_snapshot_is_restored_and_duplicate_merge_is_idempotent(tmp_path,agreement):
    first,_,_=package(tmp_path/'one',agreement,current())
    second,_,_=package(tmp_path/'two',agreement,current(attempt=2),parent=first)
    merged=merge_snapshots([second,first,first],agreement)
    assert len(merged['rows'])==845
    remaining=open_attempt(tmp_path/'three.sqlite3',agreement,current(number=50000000001),merged)
    assert remaining['remaining_model_http']==513
    assert merged['origins'][-1]['run_attempt']==2


@pytest.mark.parametrize('field',['digest','sha','run_id','attempt','expired'])
def test_artifact_identity_digest_or_attempt_mismatch_fails_closed(tmp_path,agreement,field):
    owner=current()
    _,blob,metadata=package(tmp_path/'one',agreement,owner)
    altered=deepcopy(metadata)
    attempt=1
    if field=='digest':
        altered['digest']='sha256:'+'0'*64
    elif field=='sha':
        altered['workflow_run']['head_sha']='b'*40
    elif field=='run_id':
        altered['workflow_run']['id']+=1
    elif field=='attempt':
        attempt=2
        altered['name']=f"ci-provider-ledger-{owner['run_id']}-2"
    else:
        altered['expired']=True
    with pytest.raises(ValueError):
        validate_snapshot(blob,altered,run_of(owner),attempt,agreement)


def test_divergent_concurrent_snapshots_are_never_union_deduplicated(tmp_path,agreement):
    one,_,_=package(tmp_path/'one',agreement,current(),model='first')
    two,_,_=package(tmp_path/'two',agreement,current(number=50000000001),model='second')
    with pytest.raises(ValueError,match='Divergent'):
        merge_snapshots([one,two],agreement)


@pytest.mark.parametrize('known,status,halt',[(False,200,False),(True,429,True)])
def test_unknown_usage_keeps_reservation_but_original_http_halt_stays_irreversible(tmp_path,agreement,known,status,halt):
    first,_,_=package(tmp_path/'one',agreement,current(),known=known,status=status)
    ledger=tmp_path/'next.sqlite3'
    remaining=open_attempt(ledger,agreement,current(attempt=2),first)
    assert remaining['remaining_model_http']==514
    budget=Budget(ledger,agreement['policy'])
    if halt:
        assert remaining['halt']=='http_429'
        with pytest.raises(ServiceError,match='stopped'):
            budget.reserve('llm',{})
    else:
        assert remaining['known_requests_with_missing_usage']==1
        assert remaining['remaining_output_tokens']==379122-8000
        assert budget.reserve('llm',{'max_tokens':1})==845


class MockGitHub:
    def __init__(self,agreement,now,prior=None,artifact=None,blob=None,paid=True):
        self.agreement,self.now,self.prior=agreement,now,prior
        self.artifact,self.blob,self.paid=artifact,blob,paid
        self.downloads=0

    def run(self,number,attempt):
        value=self.now if number==self.now['run_id'] else self.prior
        return run_of(dict(value,run_attempt=attempt),status='in_progress' if value==self.now else 'completed')

    def json(self,path,params=None):
        number=int(path.rsplit('/',1)[-1])
        for value in self.agreement['baseline']['runs']:
            if value['run_id']==number:
                return run_of(current(number,1,value['head_sha']))
        return self.run(number,1)

    def runs(self):
        return [run_of(self.now)]+([run_of(self.prior)] if self.prior else [])

    def artifacts(self,number):
        return [self.artifact] if self.artifact and self.prior and number==self.prior['run_id'] else []

    def jobs(self,number,attempt):
        return [{'steps':[{'name':'Paid research','status':'completed','conclusion':'failure' if self.paid else 'skipped'}]}]

    def download(self,artifact):
        self.downloads+=1
        return self.blob


def test_mock_github_failed_paid_run_without_complete_artifact_is_not_zero(tmp_path,agreement):
    api=MockGitHub(agreement,current(number=50000000001),prior=current())
    with pytest.raises(ValueError,match='lacks a complete'):
        reconcile(api,api.now,agreement,tmp_path/'new.sqlite3',api.now['head_sha'])
    assert not (tmp_path/'new.sqlite3').exists()


def test_mock_github_verified_artifact_restores_consumption_before_new_attempt(tmp_path,agreement):
    _,blob,artifact=package(tmp_path/'one',agreement,current())
    api=MockGitHub(agreement,current(number=50000000001),prior=current(),artifact=artifact,blob=blob)
    result=reconcile(api,api.now,agreement,tmp_path/'next.sqlite3',api.now['head_sha'])
    assert result['remaining']['remaining_model_http']==514 and api.downloads==1


def test_exact_sha_prerequisites_reject_pr_merge_head_mismatch_and_failed_security(agreement):
    now=current()
    api=MockGitHub(agreement,now)
    with pytest.raises(ValueError,match='PR merge/head'):
        gate_prerequisites(api,now,'b'*40,wait_seconds=0)

    def pages(path,key,params):
        security='security.yml' in path
        return [{'id':10,'run_attempt':1,'head_sha':now['head_sha'],
                 'path':'.github/workflows/security.yml' if security else '.github/workflows/ci.yml',
                 'repository':{'full_name':REPOSITORY},'status':'completed','conclusion':'failure' if security else 'success'}]
    api.pages=pages
    with pytest.raises(ValueError,match='Security failed'):
        gate_prerequisites(api,now,now['head_sha'],wait_seconds=0)


def test_exact_sha_pending_prerequisite_spends_nothing_and_success_binds_both_runs(agreement):
    now=current()
    api=MockGitHub(agreement,now)
    ready=False
    def pages(path,key,params):
        security='security.yml' in path
        return [{'id':11 if security else 10,'run_attempt':1,'head_sha':now['head_sha'],
                 'path':'.github/workflows/security.yml' if security else '.github/workflows/ci.yml',
                 'repository':{'full_name':REPOSITORY},'status':'completed' if ready else 'in_progress',
                 'conclusion':'success' if ready else None}]
    api.pages=pages
    with pytest.raises(ValueError,match='still pending'):
        gate_prerequisites(api,now,now['head_sha'],wait_seconds=0)
    ready=True
    result=gate_prerequisites(api,now,now['head_sha'],wait_seconds=0)
    assert result['supplier_http']==0 and set(result['prerequisites'])=={'CI','Security'}


def test_precheck_rejects_unamended_history_and_missing_series(tmp_path,agreement,monkeypatch):
    from scripts.check_ci_evaluation_budget import assess
    now=current()
    ledger=tmp_path/'provider-budget.sqlite3'
    missing=assess(ledger_path=ledger)
    assert missing['precheck_status']=='cumulative_reconciliation_required'
    assert missing['available_model_and_total_http'] is None and not ledger.exists()
    open_attempt(ledger,agreement,now)
    monkeypatch.setenv('GITHUB_RUN_ID',str(now['run_id']))
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT',str(now['run_attempt']))
    monkeypatch.setenv('GITHUB_SHA',now['head_sha'])
    result=assess(ledger_path=ledger)
    assert not result['cumulative_reconciliation_verified']
    assert result['available_model_and_total_http'] is None and result['exit_code']==1


def test_workflow_budget_group_is_global_and_final_artifact_includes_original_ledgers():
    workflow=(ROOT/'.github/workflows/answer-quality.yml').read_text(encoding='utf-8')
    assert 'group: deepresearch-ci-provider-cumulative-20261026' in workflow and 'cancel-in-progress: false' in workflow
    assert 'timeout-minutes: 90' in workflow and '--prerequisite-wait-seconds 1800' in workflow
    assert workflow.index('reconcile_ci_budget.py gate')<workflow.index('reconcile_ci_budget.py start')<workflow.index('Paid answers')
    assert 'ci-provider-ledger-${{ github.run_id }}-${{ github.run_attempt }}' in workflow
    assert '.cache/eval/provider-budget.sqlite3' in workflow and '.cache/eval/ci-ledger-receipt.json' in workflow


def test_cli_product_governance_uses_only_reconciled_remaining_and_keeps_product_defaults(tmp_path,agreement,monkeypatch):
    from backend.core.config import Settings
    from scripts.evaluation_scope import configure_evaluator_limits
    assert Settings(_env_file=None).subject_period_calls==200
    ledger=tmp_path/'provider-budget.sqlite3'
    open_attempt(ledger,agreement,current())
    budget=Budget(ledger,agreement['policy'])
    names=('PLATFORM_PERIOD_CALLS','SUBJECT_PERIOD_CALLS','WORKSPACE_PERIOD_CALLS','ACCOUNT_PERIOD_CALLS',
           'PLATFORM_PERIOD_TOKENS','SUBJECT_PERIOD_TOKENS','WORKSPACE_PERIOD_TOKENS','ACCOUNT_PERIOD_TOKENS')
    for name in names:
        monkeypatch.delenv(name,raising=False)
    limits=configure_evaluator_limits(budget)
    settings=Settings(_env_file=None)
    assert settings.subject_period_calls==515 and settings.workspace_period_calls==515
    assert settings.subject_period_tokens==9101235+379122
    assert limits['SUBJECT_PERIOD_CALLS']=='515'
    budget.reserve('llm',{'model':'fixture','max_tokens':10})
    assert configure_evaluator_limits(budget)['SUBJECT_PERIOD_CALLS']=='514'
    assert budget.report()['http_requests']==844


def test_missing_job_inventory_cannot_be_interpreted_as_zero_spend(tmp_path,agreement):
    api=MockGitHub(agreement,current(number=50000000001),prior=current())
    api.jobs=lambda *_:[]
    with pytest.raises(ValueError,match='inventory is incomplete'):
        reconcile(api,api.now,agreement,tmp_path/'new.sqlite3',api.now['head_sha'])
    assert not (tmp_path/'new.sqlite3').exists()
