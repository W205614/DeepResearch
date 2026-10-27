"""Static budget checks cannot spend supplier calls or mutate a reservation ledger."""
import json
import sqlite3
import socket
from pathlib import Path

import pytest

from scripts.check_ci_evaluation_budget import ROOT, assess, main


@pytest.mark.parametrize('cap,code',[(57,1),(500,0)])
def test_precheck_uses_proved_bound_without_network_or_new_ledger(tmp_path,monkeypatch,cap,code):
    def forbidden(*args,**kwargs):
        raise AssertionError('Static budget precheck attempted network access')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    policy=json.loads((ROOT/'eval/ci-provider-budget.json').read_bytes())
    policy.pop('ci_series_id',None)  # Synthetic non-CI policy tests the pure necessary bound.
    policy.update(model_http=cap,llm_http=cap,total_http=cap)
    fixture_policy=tmp_path/'policy.json'
    fixture_policy.write_text(json.dumps(policy),encoding='utf-8')
    output,ledger=tmp_path/'report.json',tmp_path/'not-created.sqlite3'
    assert main(['--policy',str(fixture_policy),'--ledger',str(ledger),'--output',str(output)])==code
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['proof_verified'] and result['critical_path_lower_bound']==245
    assert result['pass_rate_refinement_lower_bound']==296
    assert result['supplier_http_requests']==0 and not ledger.exists()
    assert json.loads(fixture_policy.read_text())==policy
    if cap==57:
        assert result['additional_http_below_critical_bound']==188
    else:
        assert result['precheck_status']=='necessary_bound_not_violated'  # No sufficiency guarantee.


def test_existing_ledger_is_read_only_and_all_requests_reduce_remaining_budget(tmp_path):
    ledger=tmp_path/'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE requests(kind TEXT)')
        db.executemany('INSERT INTO requests VALUES(?)',[('llm',),('llm',),('search',),('embedding',)])
    before=ledger.read_bytes()
    policy=json.loads((ROOT/'eval/ci-provider-budget.json').read_bytes())
    policy.pop('ci_series_id',None)
    policy.update(model_http=57,llm_http=57,total_http=57)
    path=tmp_path/'policy.json'
    path.write_text(json.dumps(policy))
    result=assess(ledger_path=ledger,policy_path=path)
    assert result['ledger']['http_requests']==4 and result['ledger']['model_requests']==3
    assert result['available_model_and_total_http']==53
    assert result['additional_http_below_critical_bound']==192
    assert ledger.read_bytes()==before


def test_fresh_workflow_retains_four_original_commands_and_uploads_failure_report():
    basis=json.loads((ROOT/'eval/ci-budget-source-basis.json').read_bytes())
    workflow=(ROOT/'.github/workflows/answer-quality.yml').read_text(encoding='utf-8')
    commands=[line.strip() for line in workflow.splitlines() if '- run: uv run python scripts/run_ci_evaluation.py paid ' in line]
    assert commands==basis['fresh_invocations'] and len(commands)==4
    precheck=workflow.index('scripts/check_ci_evaluation_budget.py')
    assert precheck<workflow.index('scripts/run_ci_evaluation.py paid ')
    step=workflow[workflow.rfind('      - name:',0,precheck):workflow.index('      - run:',precheck)]
    assert "if: steps.quality-mode.outputs.mode == 'fresh'" in step
    assert 'continue-on-error' not in step
    assert '.cache/eval/*.json' in workflow and 'if: always()' in workflow
    assert Path(ROOT/'eval/ci-provider-budget.json').is_file()
