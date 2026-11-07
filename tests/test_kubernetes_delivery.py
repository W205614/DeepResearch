"""Check deployment security and lifecycle contracts without a live cluster."""
import json
from pathlib import Path
import subprocess
import sys

from scripts.render_kubernetes import resources


def test_application_tier_has_replica_and_credential_boundaries():
    items = resources()['items']
    deployments = [item for item in items if item['kind'] == 'Deployment']
    assert {item['metadata']['name'] for item in deployments} == {'web', 'backend', 'agent', 'worker', 'document-worker'}
    for item in deployments:
        spec = item['spec']
        assert spec['replicas'] == 2
        assert spec['strategy']['rollingUpdate']['maxUnavailable'] == 0
        pod = spec['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        expected_account = 'web' if item['metadata']['name'] == 'web' else 'java' if item['metadata']['name'] == 'backend' else 'python'
        assert pod['serviceAccountName'] == 'deepresearch-' + expected_account
        container = pod['containers'][0]
        assert container['readinessProbe'] and container['livenessProbe'] and container['startupProbe']
        secrets = [entry['secretRef']['name'] for entry in container['envFrom'] if 'secretRef' in entry]
        expected = [] if item['metadata']['name'] == 'web' else ['deepresearch-java'] if item['metadata']['name'] == 'backend' else ['deepresearch-python']
        assert secrets == expected
        assert pod['terminationGracePeriodSeconds'] > 30
        if 'worker' in item['metadata']['name']:
            assert container['lifecycle']['preStop']['exec']['command'][-1] == '--drain'
    assert not any(item['kind'] == 'StatefulSet' for item in items)
    job = next(item for item in items if item['kind'] == 'Job')
    assert job['spec']['backoffLimit'] == 0
    assert job['spec']['activeDeadlineSeconds'] >= 900
    assert job['spec']['template']['spec']['serviceAccountName'] == 'deepresearch-migrator'
    assert job['spec']['template']['spec']['containers'][0]['envFrom'] == [{'secretRef': {'name': 'deepresearch-migrator'}}]
    assert {'/tmp', '/data', '/run/certs'} <= {
        mount['mountPath'] for mount in job['spec']['template']['spec']['containers'][0]['volumeMounts']}


def test_migration_job_cannot_start_with_application_kustomization():
    root = Path(__file__).resolve().parents[1] / 'deployment/kubernetes/base'
    applications = json.loads((root / 'resources.json').read_text(encoding='utf-8'))
    assert all(item['kind'] != 'Job' for item in applications['items'])
    assert json.loads((root / 'migration-job.json').read_text(encoding='utf-8'))['kind'] == 'Job'
    bootstrap = json.loads((root / 'bootstrap-resources.json').read_text(encoding='utf-8'))['items']
    assert all(item['kind'] == 'ServiceAccount' for item in bootstrap)
    assert {item['metadata']['name'] for item in bootstrap} == {
        'deepresearch-python', 'deepresearch-java', 'deepresearch-web', 'deepresearch-migrator'}


def test_generated_delivery_files_match_the_renderer_in_ci():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / 'scripts/render_kubernetes.py'), '--check'],
                   cwd=root, check=True, capture_output=True, text=True)
