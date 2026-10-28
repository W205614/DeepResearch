"""Verified append-only CI budget snapshots. No GitHub or supplier networking here."""
from __future__ import annotations

import hashlib
from contextlib import closing
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile

from scripts.bounded_evaluation import Budget

SERIES = 'deepresearch-ci-provider-20261025'
REPOSITORY = 'W205614/DeepResearch'
WORKFLOW_ID = 354007725
WORKFLOW_PATH = '.github/workflows/answer-quality.yml'
FILES = {'provider-budget.sqlite3', 'provider-budget.json', 'ci-ledger-receipt.json'}
MAX_ARTIFACT_BYTES = 16*1024*1024
REQUEST_COLUMNS = ('id','kind','label','model','payload_sha256','input_bound','output_bound','prompt_tokens','completion_tokens','status')


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def contract(root):
    authorization=json.loads((root/'eval/ci-budget-authorization.json').read_bytes())
    baseline=json.loads((root/'eval/ci-provider-baseline.json').read_bytes())
    policy=json.loads((root/'eval/ci-provider-budget.json').read_bytes())
    rows,origins=[],[]
    for run in baseline['runs']:
        for original in run['requests']:
            row=dict(original,id=len(rows)+1)
            rows.append(row)
            origins.append({'request_id':row['id'],'run_id':run['run_id'],'run_attempt':run['run_attempt'],
                'head_sha':run['head_sha'],'source_request_id':original['id']})
    history_input=sum(row['prompt_tokens'] for row in rows)
    history_output=sum(row['completion_tokens'] for row in rows)
    expected={'ci_series_id':SERIES,'model_http':authorization['new_lifetime_model_http_cap'], 'llm_http':authorization['new_lifetime_model_http_cap'],
        'total_http':authorization['new_lifetime_model_http_cap'],'embedding_http':0,'reranker_http':0,'search_http':0,
        'input_tokens':history_input+authorization['remaining_input_tokens'],
        'output_tokens':history_output+authorization['remaining_output_tokens']}
    if (baseline['series_id']!=SERIES or len(rows)!=authorization['historical_ci_consumed_model_http']
            or len(rows)+authorization['authorized_remaining_model_http']!=authorization['new_lifetime_model_http_cap']
            or authorization['new_lifetime_model_http_cap']-authorization['previous_lifetime_model_http_cap']!=authorization['additional_authorized_model_http']
            or not authorization['not_per_run'] or not authorization['do_not_reset_ledger']
            or any(row['kind']!='llm' for row in rows)):
        raise ValueError('Cumulative authorization, historical baseline or unchanged token/other budgets differ')
    original={'authorization':authorization,'authorization_sha256':digest(encoded(authorization)),
              'policy':expected,'policy_sha256':digest(encoded(expected))}
    agreement={**original,
            'baseline':baseline,'baseline_sha256':digest(encoded(baseline)),'baseline_requests':rows,'baseline_origins':origins,
            'root_authorization_sha256':original['authorization_sha256'],
            'authorization_versions':{original['authorization_sha256']:original},'amendment':None}
    amendment_path=root/'eval/ci-budget-amendment-20261029.json'
    if amendment_path.exists():
        amendment=json.loads(amendment_path.read_bytes())
        required={'schema':'ci-budget-amendment-v1','project':'DeepResearch','series_id':SERIES,
            'root_authorization_sha256':original['authorization_sha256'],
            'previous_policy_sha256':original['policy_sha256'],
            'previous_lifetime_model_http_cap':1358,'verified_consumed_model_http':1269,
            'previous_remaining_model_http':89,'additional_authorized_model_http':207,
            'new_lifetime_model_http_cap':1565,'authorized_remaining_model_http':296,
            'do_not_reset_ledger':True,'not_per_run':True,'other_budgets_unchanged':True}
        if (any(amendment.get(key)!=value for key,value in required.items())
                or not amendment.get('human_question') or not amendment.get('human_answer')
                or amendment.get('question_item_id')!=['request_user_input_async','call_d848f5c8dc9f4c2d8b08f48de1e05297',0]
                or amendment.get('baseline_sha256')!=agreement['baseline_sha256']
                or any(not re.fullmatch('[a-f0-9]{64}',amendment.get(key,''))
                       for key in ('preserved_requests_sha256','preserved_origins_sha256'))
                or amendment.get('activation_prerequisites')!={
                    'head_sha':'e97c1b2c14725d53f2e4c23736adb25d7f1e3054',
                    'runs':{'CI':40718623640,'Security':40718623740},
                    'required_conclusion':'success','fresh_source_prerequisites_still_required':True}):
            raise ValueError('Explicit cumulative budget amendment or its preserved accounting differs')
        revised=dict(expected,model_http=1565,llm_http=1565,total_http=1565)
        version={'authorization':amendment,'authorization_sha256':digest(encoded(amendment)),
                 'policy':revised,'policy_sha256':digest(encoded(revised))}
        agreement['authorization_versions'][version['authorization_sha256']]=version
        agreement.update(version,amendment=amendment)
    if policy!=agreement['policy']:
        raise ValueError('Current budget policy differs from the explicit cumulative authorization')
    return agreement


def authorization_contract(agreement, authorization_sha256):
    """Audit an original sealed artifact under its original policy, never a new cap."""
    versions=agreement['authorization_versions']
    if authorization_sha256 not in versions:
        raise ValueError('Unknown cumulative authorization')
    selected=versions[authorization_sha256]
    result={**agreement,**selected}
    if authorization_sha256==agreement['root_authorization_sha256']:
        result.update(amendment=None,authorization_versions={authorization_sha256:selected})
    return result


def amendment_record(agreement, rows, origins, *, at_request=None):
    amendment=agreement['amendment']
    anchor=amendment['verified_consumed_model_http']
    at_request=len(rows) if at_request is None else at_request
    if (type(at_request) is not int or not anchor<=at_request<=len(rows)
            or digest(encoded(rows[:anchor]))!=amendment['preserved_requests_sha256']
            or digest(encoded(origins[:anchor]))!=amendment['preserved_origins_sha256']):
        raise ValueError('Amendment requires the complete verified 1269-request accounting prefix')
    return {'authorization_sha256':agreement['authorization_sha256'],
        'previous_authorization_sha256':agreement['root_authorization_sha256'],
        'previous_lifetime_model_http_cap':amendment['previous_lifetime_model_http_cap'],
        'next_lifetime_model_http_cap':amendment['new_lifetime_model_http_cap'],
        'additional_authorized_model_http':amendment['additional_authorized_model_http'],
        'historical_requests_preserved':at_request,
        'requests_sha256':digest(encoded(rows[:at_request])),
        'origins_sha256':digest(encoded(origins[:at_request])),
        'previous_policy_sha256':amendment['previous_policy_sha256'],
        'next_policy_sha256':agreement['policy_sha256']}


def initialize(path,agreement):
    if agreement.get('amendment'):
        raise ValueError('An amended budget requires a verified existing ledger, never a fresh baseline')
    if path.exists():
        raise ValueError('A local ledger already exists; never overwrite unsealed consumption')
    budget=Budget(path,agreement['policy'],allow_ci_bootstrap=True)
    with budget.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE ci_request_origins(request_id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL,
            run_attempt INTEGER NOT NULL,head_sha TEXT NOT NULL,source_request_id INTEGER NOT NULL)''')
        db.executemany('INSERT INTO requests('+','.join(REQUEST_COLUMNS)+') VALUES('+','.join('?' for _ in REQUEST_COLUMNS)+')',
                       [tuple(row[key] for key in REQUEST_COLUMNS) for row in agreement['baseline_requests']])
        db.executemany('INSERT INTO ci_request_origins VALUES(?,?,?,?,?)',
                       [tuple(row[key] for key in ('request_id','run_id','run_attempt','head_sha','source_request_id')) for row in agreement['baseline_origins']])
        for key,value in {'ci_series':SERIES,'ci_authorization_sha256':agreement['authorization_sha256'],
                          'ci_baseline_sha256':agreement['baseline_sha256']}.items():
            db.execute('INSERT INTO state VALUES(?,?)',(key,value))
        amendment={'previous_lifetime_model_http_cap':agreement['authorization']['previous_lifetime_model_http_cap'],
                   'next_lifetime_model_http_cap':agreement['policy']['model_http'],
                   'historical_requests_preserved':len(agreement['baseline_requests']),
                   'authorization_sha256':agreement['authorization_sha256']}
        db.execute("INSERT INTO state VALUES('policy_change_authorized_20261026',?)",(json.dumps(amendment,sort_keys=True),))
        for run in agreement['baseline']['runs']:
            for index,context in enumerate(run['execution_contexts']):
                db.execute('INSERT INTO state VALUES(?,?)',(f"execution_context_baseline_{run['run_id']}_{index}",json.dumps(context,sort_keys=True)))
    return budget


def identity(environment):
    result={'repository':environment['GITHUB_REPOSITORY'],'workflow_id':WORKFLOW_ID,'workflow_path':WORKFLOW_PATH,
            'run_id':int(environment['GITHUB_RUN_ID']),'run_attempt':int(environment['GITHUB_RUN_ATTEMPT']),
            'head_sha':environment['GITHUB_SHA']}
    if result['repository']!=REPOSITORY or not re.fullmatch('[0-9a-f]{40}',result['head_sha']) or result['run_attempt']<1:
        raise ValueError('CI run identity differs from the authorized repository')
    return result


def inspect_sqlite(blob):
    # Temporary copy is only a read-only integrity probe, never a replacement for
    # a caller ledger. No SQL from the artifact is executed as instructions.
    with tempfile.TemporaryDirectory(prefix='ci-budget-inspect-') as directory:
        path=Path(directory)/'ledger.sqlite3'
        path.write_bytes(blob)
        with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise ValueError('Artifact SQLite is corrupt')
            rows=[dict(row) for row in db.execute('SELECT * FROM requests ORDER BY id')]
            origins=[dict(row) for row in db.execute('SELECT * FROM ci_request_origins ORDER BY request_id')]
            states=dict(db.execute('SELECT key,value FROM state'))
            policies=db.execute('SELECT value FROM policy').fetchall()
            if len(policies)!=1:
                raise ValueError('Artifact ledger policy is ambiguous')
            policy=json.loads(policies[0][0])
    return rows,origins,states,policy


def validate_snapshot(blob,artifact,run,attempt,agreement):
    if (artifact.get('expired') is not False or artifact.get('name')!=f"ci-provider-ledger-{run['id']}-{attempt}"
            or artifact.get('workflow_run',{}).get('id')!=run['id']
            or artifact.get('workflow_run',{}).get('head_sha')!=run['head_sha']
            or (artifact.get('workflow_run',{}).get('repository_id') is not None
                and artifact['workflow_run']['repository_id']!=run.get('repository',{}).get('id'))
            or run.get('repository',{}).get('full_name')!=REPOSITORY or run.get('workflow_id')!=WORKFLOW_ID
            or run.get('path')!=WORKFLOW_PATH or run.get('run_attempt')!=attempt or len(blob)>MAX_ARTIFACT_BYTES
            or artifact.get('digest')!='sha256:'+digest(blob)):
        raise ValueError('Artifact run/id/SHA/name/digest identity mismatch')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if set(archive.namelist())!=FILES or len(archive.namelist())!=len(FILES) or sum(item.file_size for item in archive.infolist())>MAX_ARTIFACT_BYTES:
            raise ValueError('Incomplete, duplicate or oversized budget artifact')
        files={name:archive.read(name) for name in FILES}
    receipt=json.loads(files['ci-ledger-receipt.json'])
    version=agreement['authorization_versions'].get(receipt.get('authorization_sha256'))
    if version is None:
        raise ValueError('Budget receipt uses an unknown cumulative authorization')
    expected={'repository':REPOSITORY,'workflow_id':WORKFLOW_ID,'workflow_path':WORKFLOW_PATH,
              'run_id':run['id'],'run_attempt':attempt,'head_sha':run['head_sha']}
    if (receipt.get('run')!=expected or receipt.get('series_id')!=SERIES or receipt.get('sealed') is not True
            or receipt.get('baseline_sha256')!=agreement['baseline_sha256']
            or receipt.get('policy_sha256')!=version['policy_sha256']
            or receipt.get('sqlite_sha256')!=digest(files['provider-budget.sqlite3'])
            or receipt.get('json_sha256')!=digest(files['provider-budget.json'])):
        raise ValueError('Budget receipt identity or file checksum mismatch')
    rows,origins,states,policy=inspect_sqlite(files['provider-budget.sqlite3'])
    if ('source_context_sha256' in receipt
            and receipt['source_context_sha256'] != states.get('ci_source_context_sha256')):
        raise ValueError('Source binding differs between ledger and receipt')
    report=json.loads(files['provider-budget.json'])
    if (policy!=version['policy'] or report.get('policy')!=policy or report.get('requests')!=rows
            or report.get('http_requests')!=len(rows) or receipt.get('cumulative_requests')!=len(rows)
            or receipt.get('requests_sha256')!=digest(encoded(rows))
            or receipt.get('origins_sha256')!=digest(encoded(origins))
            or states.get('ci_series')!=SERIES or states.get('ci_authorization_sha256')!=version['authorization_sha256']
            or len(rows)<len(agreement['baseline_requests'])
            or rows[:len(agreement['baseline_requests'])]!=agreement['baseline_requests']
            or origins[:len(agreement['baseline_origins'])]!=agreement['baseline_origins']):
        raise ValueError('Cumulative ledger, policy or preserved historical prefix differs')
    if version['authorization_sha256']!=agreement['root_authorization_sha256']:
        record=json.loads(states.get('policy_change_authorized_20261029','null'))
        if (not isinstance(record,dict)
                or record!=amendment_record(agreement,rows,origins,at_request=record.get('historical_requests_preserved',-1))):
            raise ValueError('Amended ledger lacks its exact original authorization transition')
    elif 'policy_change_authorized_20261029' in states:
        raise ValueError('An original authorization cannot erase an applied amendment')
    for row in rows:
        if (set(row)!=set(REQUEST_COLUMNS) or row['kind'] not in {'llm','search','embedding','reranker'}
                or any(type(row[key]) is not int or row[key]<0 for key in ('id','input_bound','output_bound'))
                or any(row[key] is not None and (type(row[key]) is not int or row[key]<0) for key in ('prompt_tokens','completion_tokens'))):
            raise ValueError('Request accounting schema or reservation is invalid')
    charged_input=sum(row['input_bound'] if row['prompt_tokens'] is None else row['prompt_tokens'] for row in rows)
    charged_output=sum(row['output_bound'] if row['completion_tokens'] is None else row['completion_tokens'] for row in rows)
    if (report.get('charged_input_tokens')!=charged_input or report.get('charged_output_tokens')!=charged_output
            or report.get('halt')!=states.get('halt')):
        raise ValueError('Report erases a conservative reservation or HTTP halt')
    if [row['id'] for row in rows]!=list(range(1,len(rows)+1)) or [row['request_id'] for row in origins]!=list(range(1,len(rows)+1)):
        raise ValueError('Missing request numbers or provenance; consumption is unknown')
    start=receipt['start_requests']
    if not len(agreement['baseline_requests'])<=start<=len(rows) or receipt['new_requests']!=len(rows)-start:
        raise ValueError('Attempt request range differs')
    if any(origin['run_id']!=run['id'] or origin['run_attempt']!=attempt or origin['head_sha']!=run['head_sha']
           or origin['source_request_id']!=index+1 for index,origin in enumerate(origins[start:])):
        raise ValueError('New request provenance belongs to another run/attempt/SHA')
    return {'rows':rows,'origins':origins,'states':states,'sqlite':files['provider-budget.sqlite3'],
            'receipt':receipt,'artifact':{'id':artifact['id'],'run_id':run['id'],'run_attempt':attempt,
                                        'head_sha':run['head_sha'],'digest':artifact['digest']}}


def merge_snapshots(snapshots,agreement):
    ranks={sha:rank for rank,sha in enumerate(agreement['authorization_versions'])}
    unique={}
    for snapshot in snapshots:
        key=(snapshot['artifact']['run_id'],snapshot['artifact']['run_attempt'])
        if key in unique and (unique[key]['rows']!=snapshot['rows'] or unique[key]['origins']!=snapshot['origins']
                or unique[key]['receipt']['authorization_sha256']!=snapshot['receipt']['authorization_sha256']
                or unique[key]['states'].get('halt')!=snapshot['states'].get('halt')):
            raise ValueError('Ambiguous duplicate attempt artifacts')
        unique[key]=snapshot
    ordered=sorted(unique.values(),key=lambda value:(len(value['rows']),value['receipt']['start_requests'],
                   ranks[value['receipt']['authorization_sha256']]))
    previous,previous_origins=agreement['baseline_requests'],agreement['baseline_origins']
    halted=False
    previous_rank=0
    for snapshot in ordered:
        if snapshot['receipt']['start_requests']>len(previous):
            raise ValueError('An earlier consuming attempt is missing from the verified artifact chain')
        if snapshot['rows'][:len(previous)]!=previous or snapshot['origins'][:len(previous_origins)]!=previous_origins:
            raise ValueError('Divergent concurrent ledger prefixes; never union/reset away double spending')
        if halted and not snapshot['states'].get('halt'):
            raise ValueError('A later snapshot removed the original irreversible HTTP halt')
        rank=ranks[snapshot['receipt']['authorization_sha256']]
        if rank<previous_rank:
            raise ValueError('A later snapshot removed the explicit budget amendment')
        previous,previous_origins=snapshot['rows'],snapshot['origins']
        halted=halted or bool(snapshot['states'].get('halt'))
        previous_rank=rank
    return ordered[-1] if ordered else None


def open_attempt(path,agreement,current,snapshot=None,artifacts=()):
    if path.exists():
        raise ValueError('Existing local ledger cannot be replaced or renumbered')
    path.parent.mkdir(parents=True,exist_ok=True)
    if snapshot:
        source=agreement['authorization_versions'].get(snapshot['receipt']['authorization_sha256'])
        if source is None:
            raise ValueError('Cannot restore an unrecognized authorization')
        transition=None
        if source['authorization_sha256']!=agreement['authorization_sha256']:
            if not agreement.get('amendment') or source['authorization_sha256']!=agreement['root_authorization_sha256']:
                raise ValueError('A ledger policy cannot be downgraded or changed without explicit authorization')
            transition=amendment_record(agreement,snapshot['rows'],snapshot['origins'])
        path.write_bytes(snapshot['sqlite'])
        if transition:
            with sqlite3.connect(path) as db:
                db.execute('BEGIN IMMEDIATE')
                policies=db.execute('SELECT value FROM policy').fetchall()
                states=dict(db.execute('SELECT key,value FROM state'))
                if (len(policies)!=1 or json.loads(policies[0][0])!=source['policy']
                        or states.get('ci_authorization_sha256')!=source['authorization_sha256']
                        or 'policy_change_authorized_20261029' in states):
                    raise ValueError('Restored policy or authorization differs before amendment')
                db.execute('UPDATE policy SET value=?',(json.dumps(agreement['policy'],sort_keys=True),))
                db.execute("UPDATE state SET value=? WHERE key='ci_authorization_sha256'",(agreement['authorization_sha256'],))
                db.execute("INSERT INTO state VALUES('policy_change_authorized_20261029',?)",(json.dumps(transition,sort_keys=True),))
        budget=Budget(path,agreement['policy'])
    else:
        budget=initialize(path,agreement)
    report=budget.report()
    with budget.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        states=dict(db.execute('SELECT key,value FROM state'))
        inherited=states.get('ci_source_context_sha256')
        if inherited:
            previous={'run':json.loads(states.get('ci_current_run','null')),'source_context_sha256':inherited}
            db.execute('INSERT INTO state VALUES(?,?)',
                (f"ci_inherited_source_context_{current['run_id']}_{current['run_attempt']}",json.dumps(previous,sort_keys=True)))
        # A copied runtime receipt belongs to its original attempt. Keep it in
        # the historical state, but require a new real preflight/bind for this run.
        db.execute("INSERT OR REPLACE INTO state VALUES('ci_source_context_sha256',NULL)")
        db.execute("INSERT OR REPLACE INTO state VALUES('ci_current_run',?)",(json.dumps(current,sort_keys=True),))
        db.execute("INSERT OR REPLACE INTO state VALUES('ci_start_requests',?)",(str(report['http_requests']),))
        db.execute("INSERT OR REPLACE INTO state VALUES('ci_verified_artifacts',?)",(json.dumps(list(artifacts),sort_keys=True),))
    return remaining(budget,agreement)


def remaining(budget,agreement):
    report=budget.report()
    model=sum(row['kind'] in {'llm','search'} for row in report['requests'])
    policy=agreement['policy']
    return {'series_id':SERIES,'cumulative_requests':report['http_requests'],
            'historical_requests':len(agreement['baseline_requests']),
            'authorized_new_requests':policy['model_http']-len(agreement['baseline_requests']),
            'new_requests_across_all_runs':report['http_requests']-len(agreement['baseline_requests']),
            'remaining_model_http':max(0,policy['model_http']-model),
            'remaining_total_http':max(0,policy['total_http']-report['http_requests']),
            'remaining_input_tokens':max(0,policy['input_tokens']-report['charged_input_tokens']),
            'remaining_output_tokens':max(0,policy['output_tokens']-report['charged_output_tokens']),
            'known_requests_with_missing_usage':report['usage_missing_requests'],
            'halt':report['halt'],'supplier_calls_by_reconciliation':0}


def seal(path,agreement,current,output):
    if not path.exists():
        raise ValueError('No reconciled ledger exists; cannot seal an invented zero-spend artifact')
    budget=Budget(path,agreement['policy'])
    with budget.connect() as db:
        states=dict(db.execute('SELECT key,value FROM state'))
        if json.loads(states.get('ci_current_run','null'))!=current:
            raise ValueError('Sealing another run/attempt ledger is forbidden')
        db.row_factory=sqlite3.Row
        origins=[dict(row) for row in db.execute('SELECT * FROM ci_request_origins ORDER BY request_id')]
    report=budget.report()
    start=int(states['ci_start_requests'])
    if len(origins)!=report['http_requests']:
        raise ValueError('A supplier reservation lacks persistent run provenance')
    output.mkdir(parents=True,exist_ok=True)
    json_bytes=json.dumps(report,ensure_ascii=False,indent=2).encode()+b'\n'
    (output/'provider-budget.json').write_bytes(json_bytes)
    receipt={'schema':'ci-provider-ledger-receipt-v1','sealed':True,'series_id':SERIES,'run':current,
        'authorization_sha256':agreement['authorization_sha256'],'baseline_sha256':agreement['baseline_sha256'],
        'policy_sha256':agreement['policy_sha256'],'start_requests':start,'cumulative_requests':report['http_requests'],
        'new_requests':report['http_requests']-start,'requests_sha256':digest(encoded(report['requests'])),
        'origins_sha256':digest(encoded(origins)),'sqlite_sha256':digest(path.read_bytes()),'json_sha256':digest(json_bytes),
        'verified_parent_artifacts':json.loads(states.get('ci_verified_artifacts','[]')),
        'source_context_sha256':states.get('ci_source_context_sha256'),
        'remaining':remaining(budget,agreement)}
    (output/'ci-ledger-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt
