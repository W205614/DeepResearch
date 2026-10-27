"""Host Git provenance bound to the reconciled ledger; paid workers need no git."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from scripts.ci_budget_ledger import encoded, digest

CONTEXT = Path('.cache/eval/ci-source-context.json')
PREFLIGHT = Path('.cache/eval/ci-python-preflight.json')


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_preflight(value):
    if (value.get('passed') is not True or value.get('python_version') != '3.11.18'
            or value.get('compromised_ca_verify_code') != 28 or value.get('valid_tls') not in {'TLSv1.2', 'TLSv1.3'}
            or type(value.get('uid')) is not int or value['uid'] <= 0
            or type(value.get('gid')) is not int or value['gid'] <= 0
            or not isinstance(value.get('image_source_files'), dict) or not value['image_source_files']
            or value.get('source_sha256') != '9120e0a653868f6e3698d36cdd99e5a58903ec9469e448f21d98f7b16d8fa290'
            or value.get('supplier_http') != 0 or value.get('network_http') != 0):
        raise ValueError('Actual interpreter/CA preflight is missing or failed')


def bind_source(root, budget, current):
    preflight_path = root/PREFLIGHT
    preflight = json.loads(preflight_path.read_bytes())
    _verify_preflight(preflight)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip())
    if head != current['head_sha'] or dirty:
        raise ValueError('CI source must be the exact clean checked-out SHA')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    hashes = {}
    for name in filter(None, tracked):
        path = root/name
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Tracked source escapes checkout or is not a file')
        hashes[name] = fingerprint(path)
    for name, sha in preflight['image_source_files'].items():
        if hashes.get(name) != sha:
            raise ValueError('Built image source differs from clean checkout: '+name)
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', preflight.get('runtime_image_id', '')):
        raise ValueError('Preflight has no immutable runtime image identity')
    context = {'schema': 'ci-source-context-v1', 'run': current, 'checkout_sha': head, 'working_tree_dirty': False,
               'tracked_files': hashes, 'preflight_sha256': fingerprint(preflight_path),
               'runtime_image_id': preflight['runtime_image_id'], 'execution_uid': preflight['uid'], 'execution_gid': preflight['gid']}
    payload = encoded(context)
    sha = digest(payload)
    key = f"ci_source_context_{current['run_id']}_{current['run_attempt']}"
    with budget.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        state = dict(connection.execute('SELECT key,value FROM state'))
        if json.loads(state.get('ci_current_run', 'null')) != current:
            raise ValueError('Source context differs from reconciled run identity')
        if key in state and state[key] != sha:
            raise ValueError('An attempt source context cannot be replaced')
        connection.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, sha))
        connection.execute("INSERT OR REPLACE INTO state VALUES('ci_source_context_sha256',?)", (sha,))
    destination = root/CONTEXT
    destination.write_bytes(payload)
    return {'sha256': sha, 'files': len(hashes), 'runtime_image_id': context['runtime_image_id']}


def verify_bound_source(root, budget, path=None, *, require_runtime=True):
    path = root/CONTEXT if path is None else path
    payload = path.read_bytes()
    context = json.loads(payload)
    with budget.connect() as connection:
        state = dict(connection.execute('SELECT key,value FROM state'))
    current = json.loads(state.get('ci_current_run', 'null'))
    if (context.get('schema') != 'ci-source-context-v1' or context.get('run') != current
            or context.get('working_tree_dirty') is not False or not current
            or context.get('checkout_sha') != current['head_sha']
            or digest(payload) != state.get('ci_source_context_sha256')
            or digest(payload) != state.get(f"ci_source_context_{current['run_id']}_{current['run_attempt']}")):
        raise ValueError('Source receipt is not bound to this reconciled run/attempt/SHA')
    if not isinstance(context.get('tracked_files'), dict) or not context['tracked_files']:
        raise ValueError('Source receipt has no tracked byte inventory')
    for name, expected in context['tracked_files'].items():
        source = root/name
        if (not source.resolve().is_relative_to(root.resolve()) or not source.is_file()
                or fingerprint(source) != expected):
            raise ValueError('Source bytes changed after provenance binding: '+name)
    preflight_path = root/PREFLIGHT
    if fingerprint(preflight_path) != context['preflight_sha256']:
        raise ValueError('Interpreter preflight receipt changed')
    preflight = json.loads(preflight_path.read_bytes())
    _verify_preflight(preflight)
    if (preflight['runtime_image_id'] != context['runtime_image_id']
            or preflight['uid'] != context['execution_uid'] or preflight['gid'] != context['execution_gid']):
        raise ValueError('Runtime identity/ownership differs from source receipt')
    if require_runtime:
        from scripts.ci_python_preflight import runtime_identity
        import os
        runtime_identity()  # Real interpreter and signed-source file; env markers are insufficient.
        if os.getuid() != context['execution_uid'] or os.getgid() != context['execution_gid']:
            raise ValueError('Paid process ownership differs from the preflight')
    return context


def evaluation_revision(root):
    """CI reports consume ledger-bound provenance; local evaluation keeps Git metadata."""
    import os
    import sqlite3
    from contextlib import closing
    ledger = os.environ.get('EVALUATION_LEDGER')
    if ledger:
        with closing(sqlite3.connect(ledger)) as connection:
            policy = json.loads(connection.execute('SELECT value FROM policy').fetchone()[0])
        if policy.get('ci_series_id'):
            from scripts.bounded_evaluation import Budget
            context = verify_bound_source(root, Budget(ledger, policy))
            return context['checkout_sha'], False
    return (subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
            bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                         cwd=root, text=True).strip()))
