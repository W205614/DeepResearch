"""Read-only checks for the local Compose deployment; no model calls or secrets in output."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Deployment check command failed; output withheld")
    return result.stdout.strip()


PROBE = '''
import json, os, httpx
c=httpx.Client(timeout=10)
assert c.get('http://backend:8000/readyz').json()=={'status':'ready'}
assert c.get('http://backend:8000/api/capabilities').status_code==401
assert os.environ.get('ALLOW_INTERNAL_MODEL_PROCESSING','false').lower()=='false'
targets=c.get('http://prometheus:9090/api/v1/targets').json()['data']['activeTargets']
jobs={t['labels']['job']:t['health'] for t in targets}
for job in ('deepresearch-api','deepresearch-worker','deepresearch-document-worker'):
    assert jobs.get(job)=='up', (job,jobs.get(job))
rules=c.get('http://prometheus:9090/api/v1/rules').json()['data']['groups']
names=[r['name'] for group in rules for r in group['rules']]
assert 'DeepResearchServiceDown' in names
print(json.dumps({'core_ready':True,'anonymous_capabilities_denied':True,
    'internal_model_processing':False,'scrape_targets':jobs,'loaded_alert_rules':len(names)}))
'''


def main():
    command(sys.executable, 'scripts/probe_external.py', '--base-url', 'http://127.0.0.1:8080')
    version = command('docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U',
                      'deepresearch', '-d', 'deepresearch', '-Atc', 'select version_num from alembic_version;')
    if version != '0008_reliability':
        raise RuntimeError('Unexpected database migration revision')
    report = json.loads(command('docker', 'compose', 'exec', '-T', 'backend', 'python', '-c', PROBE))
    report.update(migration=version, passed=True, scope='current single-host deployment; no external alert delivery tested')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
