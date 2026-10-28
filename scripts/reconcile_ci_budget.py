"""Reconcile immutable GitHub run-attempt artifacts before any CI supplier call."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.ci_budget_ledger import (  # noqa: E402
    contract, identity, merge_snapshots, open_attempt, seal, validate_snapshot,
    REPOSITORY, WORKFLOW_ID, WORKFLOW_PATH, MAX_ARTIFACT_BYTES, SERIES)


class GitHub:
    def __init__(self,token,client=None):
        if not token:
            raise ValueError('Read-only GitHub Actions token is required for cumulative accounting')
        self.client=client or httpx.Client(base_url='https://api.github.com',timeout=30,trust_env=False,
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})

    def json(self,path,params=None):
        response=self.client.get('/repos/'+REPOSITORY+path,params=params)
        response.raise_for_status()
        return response.json()

    def pages(self,path,key,params=None):
        output=[]
        for page in range(1,101):
            value=self.json(path,{**(params or {}),'per_page':100,'page':page})
            rows=value[key]
            output.extend(rows)
            if len(rows)<100:
                return output
        raise ValueError('GitHub inventory exceeds the bounded complete audit; consumption cannot be assumed zero')

    def runs(self):
        return self.pages('/actions/workflows/answer-quality.yml/runs','workflow_runs')

    def run(self,run_id,attempt):
        return self.json(f'/actions/runs/{run_id}/attempts/{attempt}')

    def jobs(self,run_id,attempt):
        return self.pages(f'/actions/runs/{run_id}/attempts/{attempt}/jobs','jobs')

    def artifacts(self,run_id):
        return self.pages(f'/actions/runs/{run_id}/artifacts','artifacts')

    def download(self,artifact):
        response=self.client.get('/repos/'+REPOSITORY+f"/actions/artifacts/{artifact['id']}/zip",follow_redirects=False)
        if response.status_code==302:
            target=httpx.URL(response.headers['location'])
            if target.scheme!='https' or target.username or target.password:
                raise ValueError('Invalid artifact download redirect')
            # Signed storage download never receives the GitHub bearer token.
            with httpx.Client(timeout=60,trust_env=False) as storage:
                with storage.stream('GET',target) as stream:
                    stream.raise_for_status()
                    chunks,size=[],0
                    for chunk in stream.iter_bytes():
                        size+=len(chunk)
                        if size>MAX_ARTIFACT_BYTES:
                            raise ValueError('Budget artifact exceeds download bound')
                        chunks.append(chunk)
                    return b''.join(chunks)
        response.raise_for_status()
        if len(response.content)>MAX_ARTIFACT_BYTES:
            raise ValueError('Budget artifact exceeds download bound')
        return response.content


def paid_started(jobs):
    markers=('scripts/bounded_evaluation.py','scripts/evaluate_answers.py','scripts/evaluate_research.py')
    return any(step.get('conclusion')!='skipped' and step.get('status') in {'in_progress','completed'}
        and (step.get('name','').startswith('Paid ') or any(marker in step.get('name','') for marker in markers))
        for job in jobs for step in job.get('steps',[]))


def audit_current(api,current,checkout_sha):
    run=api.run(current['run_id'],current['run_attempt'])
    if (run.get('repository',{}).get('full_name')!=REPOSITORY or run.get('workflow_id')!=WORKFLOW_ID
            or run.get('path')!=WORKFLOW_PATH or run.get('id')!=current['run_id']
            or run.get('run_attempt')!=current['run_attempt']
            or run.get('head_sha')!=current['head_sha'] or checkout_sha!=current['head_sha']):
        raise ValueError('Checkout/GITHUB_SHA/API head_sha do not bind one exact source; PR merge/head results cannot be substituted')
    return run


def audit_amendment_prerequisites(api,agreement):
    amendment=agreement.get('amendment')
    if not amendment:
        return
    condition=amendment['activation_prerequisites']
    for name,number in condition['runs'].items():
        run=api.json(f'/actions/runs/{number}')
        path='.github/workflows/'+('ci.yml' if name=='CI' else 'security.yml')
        if (run.get('id')!=number or run.get('repository',{}).get('full_name')!=REPOSITORY
                or run.get('head_sha')!=condition['head_sha'] or run.get('path')!=path
                or run.get('status')!='completed' or run.get('conclusion')!=condition['required_conclusion']):
            raise ValueError('Budget amendment waits for the explicitly required exact-source CI and Security success')


def reconcile(api,current,agreement,ledger,checkout_sha):
    audit_current(api,current,checkout_sha)
    audit_amendment_prerequisites(api,agreement)
    baseline={(run['run_id'],run['run_attempt']) for run in agreement['baseline']['runs']}
    frontier=min(run_id for run_id,_ in baseline)
    runs={}
    for run in api.runs():
        if run['id']>=frontier:
            runs[run['id']]=run
        elif int(run.get('run_attempt',1))>1:
            if not run.get('updated_at'):
                raise ValueError('An older rerun has no update time; its in-scope consumption is unknown')
            if run['updated_at']>=agreement['baseline']['authorization_scope_start_utc']:
                runs[run['id']]=run
    # Explicitly inspect baseline/current attempts even if pagination or ordering
    # differs. Re-running an old paid baseline must never disappear from accounting.
    for run_id,attempt in baseline:
        existing=api.json(f'/actions/runs/{run_id}')
        runs[run_id]=existing
    if current['run_id'] not in runs:
        runs[current['run_id']]=api.json(f"/actions/runs/{current['run_id']}")
    snapshots,not_started=[],[]
    for candidate in runs.values():
        artifacts=api.artifacts(candidate['id'])
        for attempt in range(1,int(candidate['run_attempt'])+1):
            key=(candidate['id'],attempt)
            if key in baseline or key==(current['run_id'],current['run_attempt']):
                continue
            run=api.run(candidate['id'],attempt)
            jobs=api.jobs(candidate['id'],attempt)
            if not jobs or any(not isinstance(job.get('steps'),list) or not job['steps'] for job in jobs):
                raise ValueError('Attempt job/step inventory is incomplete; unknown consumption is not zero')
            matches=[artifact for artifact in artifacts if artifact.get('name')==f"ci-provider-ledger-{candidate['id']}-{attempt}"]
            started=paid_started(jobs)
            if started and run.get('status')!='completed':
                raise ValueError('Another consuming attempt is active; cumulative serialization is not established')
            if not matches:
                if started:
                    raise ValueError(f"Paid attempt {candidate['id']}/{attempt} lacks a complete original ledger artifact; unknown consumption is not zero")
                not_started.append({'run_id':candidate['id'],'run_attempt':attempt,'paid_started':False})
                continue
            for artifact in matches:
                if not artifact.get('workflow_run') or not artifact.get('digest'):
                    artifact=api.json(f"/actions/artifacts/{artifact['id']}")
                snapshots.append(validate_snapshot(api.download(artifact),artifact,run,attempt,agreement))
    latest=merge_snapshots(snapshots,agreement)
    verified={(value['artifact']['run_id'],value['artifact']['run_attempt']):value['artifact']['head_sha'] for value in snapshots}
    if latest:
        for origin in latest['origins'][len(agreement['baseline_requests']):]:
            if verified.get((origin['run_id'],origin['run_attempt']))!=origin['head_sha']:
                raise ValueError('A request origin lacks a complete verified run/attempt/SHA artifact')
    sources=list({value['artifact']['id']:value['artifact'] for value in snapshots}.values())
    result=open_attempt(ledger,agreement,current,latest,sources)
    return {'run':current,'authorization_sha256':agreement['authorization_sha256'],
            'verified_artifacts':sources,'proved_not_started':not_started,'remaining':result,'supplier_http':0}


def gate_prerequisites(api,current,checkout_sha,wait_seconds=900,sleep=time.sleep,clock=time.monotonic):
    audit_current(api,current,checkout_sha)
    deadline=clock()+wait_seconds
    previous=None
    while True:
        result={}
        for name,path in [('CI','.github/workflows/ci.yml'),('Security','.github/workflows/security.yml')]:
            filename=path.rsplit('/',1)[-1]
            runs=api.pages(f'/actions/workflows/{filename}/runs','workflow_runs',{'head_sha':current['head_sha']})
            matches=[run for run in runs if run.get('head_sha')==current['head_sha'] and run.get('path')==path
                     and run.get('repository',{}).get('full_name')==REPOSITORY]
            latest=max(matches,key=lambda run:(run['id'],run.get('run_attempt',1))) if matches else None
            result[name]=None if latest is None else {key:latest.get(key) for key in ('id','run_attempt','head_sha','status','conclusion','html_url')}
        if all(value and value['status']=='completed' and value['conclusion']=='success' for value in result.values()):
            return {'source_sha':current['head_sha'],'checkout_sha':checkout_sha,'prerequisites':result,'supplier_http':0}
        if result!=previous:
            print(json.dumps({'event':'paid_prerequisites_wait','results':result}),flush=True)
            previous=result
        if any(value and value['status']=='completed' and value['conclusion']!='success' for value in result.values()):
            raise ValueError('Exact-source CI or Security failed; supplier HTTP remains zero')
        if clock()>=deadline:
            raise ValueError('Exact-source CI/Security still pending; fail before spending and rerun this same SHA later')
        sleep(min(15,max(0,deadline-clock())))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['reconcile','bind','gate','start','seal'])
    parser.add_argument('--accounting-only',action='store_true',
                        help='Reconcile and precheck before building; bind the real interpreter later before any paid call')
    parser.add_argument('--ledger',type=Path,default=ROOT/'.cache/eval/provider-budget.sqlite3')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'.cache/eval')
    parser.add_argument('--prerequisite-wait-seconds',type=int,default=900)
    args=parser.parse_args(argv)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    try:
        if args.accounting_only and args.mode!='reconcile':
            raise ValueError('--accounting-only is only valid for reconciliation')
        agreement=contract(ROOT)
        current=identity(os.environ)
        if args.mode=='seal':
            result=seal(args.ledger,agreement,current,args.output_dir)
        elif args.mode=='start':
            from scripts.bounded_evaluation import Budget
            from scripts.ci_source_context import verify_bound_source
            verify_bound_source(ROOT, Budget(args.ledger, agreement['policy']), require_runtime=False)
            if not os.environ.get('LLM_API_KEY'):
                raise ValueError('Model credential is missing; do not begin a paid attempt')
            prerequisite=json.loads((args.output_dir/'ci-prerequisites.json').read_bytes())
            if prerequisite['source_sha']!=current['head_sha']:
                raise ValueError('Prerequisite source SHA differs')
            reconciliation=json.loads((args.output_dir/'ci-reconciliation.json').read_bytes())
            if reconciliation['run']!=current:
                raise ValueError('Reconciliation identity differs')
            result={'series_id':SERIES,'run':current,'authorization_sha256':agreement['authorization_sha256'],
                    'start_requests':reconciliation['remaining']['cumulative_requests'],'supplier_http':0}
            (args.output_dir/'ci-paid-start.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        else:
            api=GitHub(os.environ.get('GITHUB_TOKEN',''))
            checkout_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
            if args.mode=='reconcile':
                result=reconcile(api,current,agreement,args.ledger,checkout_sha)
                if not args.accounting_only:
                    from scripts.bounded_evaluation import Budget
                    from scripts.ci_source_context import bind_source
                    result['source_context']=bind_source(ROOT, Budget(args.ledger, agreement['policy']), current)
                (args.output_dir/'ci-reconciliation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            elif args.mode=='bind':
                audit_current(api,current,checkout_sha)
                from scripts.bounded_evaluation import Budget
                from scripts.ci_source_context import bind_source
                result=json.loads((args.output_dir/'ci-reconciliation.json').read_bytes())
                if result.get('run')!=current or result.get('authorization_sha256')!=agreement['authorization_sha256']:
                    raise ValueError('Interpreter binding requires this exact reconciled run and cumulative authorization')
                result['source_context']=bind_source(ROOT,Budget(args.ledger,agreement['policy']),current)
                (args.output_dir/'ci-reconciliation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            else:
                result=gate_prerequisites(api,current,checkout_sha,args.prerequisite_wait_seconds)
                (args.output_dir/'ci-prerequisites.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'mode':args.mode,'supplier_http':0,'result':'verified'}))
        return 0
    except Exception as exc:
        # Do not print private HTTP driver bodies, tokens or response headers.
        error={'mode':args.mode,'status':'failed_closed','error_type':type(exc).__name__,
               'reason':str(exc) if isinstance(exc,ValueError) else 'GitHub inventory/artifact or local ledger could not be verified',
               'supplier_http':0}
        (args.output_dir/('ci-ledger-'+args.mode+'-failure.json')).write_text(json.dumps(error,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(error))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
