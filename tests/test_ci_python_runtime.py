"""Offline source/ledger/runtime boundary counterexamples; no supplier access."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bounded_evaluation import Budget
from scripts.ci_budget_ledger import contract, open_attempt
from scripts import ci_python_preflight as probe
from scripts import ci_source_context as source
from scripts import run_ci_evaluation as runner
from tests.test_ci_cumulative_budget import current

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'sha256:'+'a'*64


@pytest.fixture
def bound(tmp_path, monkeypatch):
    root = tmp_path/'checkout'
    root.mkdir()
    (root/'app.py').write_bytes(b'print("source")\n')
    (root/source.PREFLIGHT).parent.mkdir(parents=True)
    preflight = dict(passed=True, python_version='3.11.18', compromised_ca_verify_code=28,
                     source_sha256=probe.SOURCE_SHA, valid_tls='TLSv1.3', uid=10001, gid=10001,
                     supplier_http=0, network_http=0, runtime_image_id=IMAGE,
                     image_source_files={'app.py':source.fingerprint(root/'app.py')})
    (root/source.PREFLIGHT).write_text(json.dumps(preflight), encoding='utf-8')
    def git(arguments, **kwargs):
        if arguments[1]=='rev-parse':
            return current()['head_sha']+'\n'
        if arguments[1]=='status':
            return ''
        assert arguments[1]=='ls-files'
        return b'app.py\0'
    monkeypatch.setattr(source.subprocess, 'check_output', git)
    agreement=contract(ROOT)
    ledger=root/'.cache/eval/provider-budget.sqlite3'
    open_attempt(ledger, agreement, current())
    budget=Budget(ledger, agreement['policy'])
    return root, budget, preflight


def test_source_receipt_requires_clean_git_and_binds_exact_ledger_attempt(bound, monkeypatch):
    root, budget, _ = bound
    source.bind_source(root, budget, current())
    context=source.verify_bound_source(root, budget, require_runtime=False)
    assert context['checkout_sha']==current()['head_sha']
    assert budget.report()['http_requests']==843
    with budget.connect() as db:
        db.execute("UPDATE state SET value=? WHERE key='ci_current_run'", (json.dumps(current(attempt=2)),))
    with pytest.raises(ValueError,match='run/attempt/SHA'):
        source.verify_bound_source(root, budget, require_runtime=False)


@pytest.mark.parametrize('attack', ['source', 'context', 'preflight', 'ledger_digest', 'missing'])
def test_tampering_rejected_before_transport(bound, attack):
    root,budget,_=bound
    source.bind_source(root,budget,current())
    if attack=='source':
        (root/'app.py').write_bytes(b'changed')
    elif attack=='context':
        (root/source.CONTEXT).write_bytes(b'{}')
    elif attack=='preflight':
        (root/source.PREFLIGHT).write_bytes(b'{}')
    elif attack=='ledger_digest':
        with budget.connect() as db:
            db.execute("UPDATE state SET value='forged' WHERE key='ci_source_context_sha256'")
    else:
        # An absent independently mounted receipt cannot be replaced by environment flags.
        source_path=root/'missing-context.json'
        with pytest.raises(FileNotFoundError):
            source.verify_bound_source(root,budget,source_path,require_runtime=False)
        return
    with pytest.raises(ValueError):
        source.verify_bound_source(root,budget,require_runtime=False)
    assert budget.report()['http_requests']==843


@pytest.mark.parametrize('attack',['dirty','sha','image_bytes','version','ca','root','http'])
def test_host_binding_refuses_unproved_source_or_interpreter(bound,monkeypatch,attack):
    root,budget,preflight=bound
    original=source.subprocess.check_output
    if attack in {'dirty','sha'}:
        def git(arguments,**kwargs):
            if arguments[1]==('status' if attack=='dirty' else 'rev-parse'):
                return 'M app.py' if attack=='dirty' else 'b'*40
            return original(arguments,**kwargs)
        monkeypatch.setattr(source.subprocess,'check_output',git)
    else:
        if attack=='image_bytes':
            preflight['image_source_files']['app.py']='0'*64
        if attack=='version':
            preflight['python_version']='3.11.15'
        if attack=='ca':
            preflight['compromised_ca_verify_code']=20
        if attack=='root':
            preflight['uid']=0
        if attack=='http':
            preflight['network_http']=1
        (root/source.PREFLIGHT).write_text(json.dumps(preflight),encoding='utf-8')
    with pytest.raises(ValueError):
        source.bind_source(root,budget,current())
    assert budget.report()['http_requests']==843


def test_ci_transport_cannot_install_on_unverified_host(bound,monkeypatch):
    _,budget,_=bound
    with pytest.raises(ValueError,match='verified interpreter'):
        budget.install()
    monkeypatch.setattr(probe.sys,'version_info',(3,11,15))
    with pytest.raises(ValueError,match='actual non-root'):
        probe.runtime_identity()


def test_ci_report_revision_uses_bound_receipt_without_git(bound,monkeypatch):
    root,budget,_=bound
    source.bind_source(root,budget,current())
    def forbidden(*args,**kwargs):
        raise AssertionError('Paid container must not require Git')
    monkeypatch.setattr(source.subprocess,'check_output',forbidden)
    monkeypatch.setattr(probe,'runtime_identity',lambda:{})
    monkeypatch.setattr(probe.os,'getuid',lambda:10001,raising=False)
    monkeypatch.setattr(probe.os,'getgid',lambda:10001,raising=False)
    monkeypatch.setenv('EVALUATION_LEDGER',str(budget.path))
    assert source.evaluation_revision(root)==(current()['head_sha'],False)


def test_container_security_and_frozen_arguments(tmp_path):
    preflight=runner.command(tmp_path,IMAGE,10001,10001,preflight=True)
    assert preflight[preflight.index('--network')+1]=='none'
    assert all(name not in preflight for name in (*runner.FORWARD,'GITHUB_TOKEN'))
    arguments=['--only','conflict-1,compare-3,single-5','--repetitions','3','--require-all',
               '--debug-fixed-failures','--output','.cache/eval/research-stability.json']
    paid=runner.command(tmp_path,IMAGE,10001,10001,label='stability',target='scripts/evaluate_research.py',arguments=arguments)
    assert paid[-len(arguments):]==arguments and 'GITHUB_TOKEN' not in paid
    assert '--read-only' in paid and paid[paid.index('--user')+1]=='10001:10001'
    assert paid[paid.index('--cap-drop')+1]=='ALL'
    mounts=[paid[index+1] for index,value in enumerate(paid) if value=='--mount']
    assert len(mounts)==4 and sum(value.endswith(',readonly') for value in mounts)==3
    assert all('GITHUB_TOKEN' not in value for value in mounts)
    for uid,image in [(0,IMAGE),(10001,'mutable:latest')]:
        with pytest.raises(ValueError):
            runner.command(tmp_path,image,uid,10001,preflight=True)


def test_cli_subparsers_preserve_paid_flags_without_passing_host_tokens(bound,monkeypatch):
    root,budget,_=bound
    source.bind_source(root,budget,current())
    monkeypatch.setattr(runner,'ROOT',root)
    calls=[]
    monkeypatch.setattr(runner.subprocess,'run',lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    assert runner.main(['paid','--uid','10001','--gid','10001','--label','extensions',
                        'scripts/evaluate_research.py','--cases','eval/research_quality_extensions.json'])==0
    assert calls[0][-3:]==['scripts/evaluate_research.py','--cases','eval/research_quality_extensions.json']
    assert IMAGE in calls[0] and 'GITHUB_TOKEN' not in calls[0]


def test_workflow_preflight_precedes_start_and_only_paid_targets_use_container():
    workflow=(ROOT/'.github/workflows/answer-quality.yml').read_text(encoding='utf-8')
    assert workflow.index('run_ci_evaluation.py preflight')<workflow.index('reconcile_ci_budget.py reconcile')
    assert workflow.index('reconcile_ci_budget.py gate')<workflow.index('reconcile_ci_budget.py start')
    assert workflow.index('reconcile_ci_budget.py start')<workflow.index('run_ci_evaluation.py paid')
    assert workflow.count('- run: uv run python scripts/run_ci_evaluation.py paid ')==4
    assert 'persist-credentials: false' in workflow and 'timeout-minutes: 90' in workflow
