"""Render the application-only Kubernetes baseline using standard API objects.

Edit configuration in the environment overlay, never generated resources.json.
No cloud resources, credentials or running cluster are needed for rendering.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'deployment/kubernetes/base/resources.json'


def resources():
    result = []

    def add(kind, name, *, version='v1', **fields):
        item = {'apiVersion': version, 'kind': kind, 'metadata': {'name': name}, **fields}
        result.append(item)
        return item

    for role in ('python', 'java', 'web', 'migrator'):
        add('ServiceAccount', 'deepresearch-' + role, automountServiceAccountToken=False)
    add('ConfigMap', 'deepresearch-python', data={
        'AUTH_MODE': 'oidc', 'DEMO_MODE': 'false', 'QUEUE_BACKEND': 'redis',
        'DOCUMENT_SCAN_MODE': 'clamav', 'CHECKPOINT_SCHEMA_MANAGED': 'true', 'DATA_DIR': '/data',
        'INTERNAL_SERVICE_TOKEN_FILE': '/run/internal/internal_service_token',
        'OIDC_ISSUER': 'https://REPLACE_IDENTITY_ORIGIN/tenant', 'OIDC_AUDIENCE': 'deepresearch-api',
        'OIDC_JWKS_URL': 'https://REPLACE_IDENTITY_ORIGIN/tenant/keys', 'OIDC_CLIENT_ID': 'deepresearch-web',
        'OIDC_CA_CERTS': '/run/certs/oidc-ca.pem',
        'OIDC_SCOPES': 'openid profile email', 'OIDC_REGISTRATION_ENABLED': 'false',
        'REDIS_SSL_CA_CERTS': '/run/certs/redis-ca.pem',
        'MILVUS_DB_NAME': 'default', 'MILVUS_SERVER_PEM_PATH': '/run/certs/milvus-ca.pem',
        'VECTOR_COLLECTION_PREFIX': 'dr', 'CLAMAV_HOST': 'REPLACE_PRIVATE_SCANNER', 'CLAMAV_PORT': '3310',
        'OBJECT_STORE_BACKEND': 's3', 'OBJECT_STORE_REGION': 'REPLACE_REGION',
        'OBJECT_STORE_BUCKET': 'REPLACE_EXISTING_BUCKET', 'OBJECT_STORE_AUTO_CREATE_BUCKET': 'false',
        'OBJECT_STORE_ADDRESSING_STYLE': 'auto', 'AWS_CA_BUNDLE': '/run/certs/s3-ca.pem',
        'LLM_PROVIDER': 'openai', 'LLM_BASE_URL': 'https://api.openai.com/v1',
        'LLM_MODEL_ID': 'gpt-6.1-sol', 'VISION_MODEL_ID': 'gpt-6.1-sol',
        'LLM_REASONING_EFFORT': 'low', 'LLM_EXTRA_BODY': '{}', 'WEB_SEARCH_PROVIDER': 'openai',
        'EMBEDDING_BASE_URL': 'https://api.openai.com/v1', 'EMBEDDING_MODEL': 'text-embedding-3-large',
        'EMBEDDING_DIMENSION': '3072', 'SEND_EMBEDDING_DIMENSIONS': 'true',
        'RERANKER_ENABLED': 'false', 'RERANKER_URL': 'https://api.siliconflow.cn/v1/rerank',
        'RERANKER_MODEL': 'Pro/BAAI/bge-reranker-v2-m3', 'ALLOW_INTERNAL_MODEL_PROCESSING': 'false',
        'PLATFORM_OPERATOR_SUBJECTS': '', 'PLATFORM_ACTIVE_RUN_LIMIT': '5', 'MAX_QUEUED_RUNS': '20',
        'WORKSPACE_CONCURRENT_RUN_LIMIT': '2', 'MAX_CONCURRENT_RUNS': '3', 'RESEARCH_WORKER_MAX_JOBS': '3',
        'WORKER_DRAIN_SECONDS': '30', 'WORKSPACE_DOCUMENT_LIMIT': '500', 'WORKSPACE_CHUNK_LIMIT': '50000',
        'RETENTION_ARCHIVE_AFTER_DAYS': '0', 'RETENTION_CACHE_DAYS': '7',
        'OTEL_EXPORTER_OTLP_ENDPOINT': '', 'OTEL_EXPORTER_OTLP_CERTIFICATE': '/run/certs/otlp-ca.pem',
    })
    add('ConfigMap', 'deepresearch-java', data={
        'AUTH_MODE': 'oidc', 'OIDC_ISSUER': 'https://REPLACE_IDENTITY_ORIGIN/tenant',
        'OIDC_JWKS_URL': 'https://REPLACE_IDENTITY_ORIGIN/tenant/keys', 'OIDC_AUDIENCE': 'deepresearch-api',
        'OIDC_CLIENT_ID': 'deepresearch-web', 'OIDC_SCOPES': 'openid profile email',
        'OIDC_REGISTRATION_ENABLED': 'false', 'SPRING_DATASOURCE_USERNAME': 'dr_java',
        'SPRING_APPLICATION_JSON': json.dumps({'management': {'metrics': {'distribution': {'percentiles-histogram': {'http.server.requests': True}}}}}),
        'AGENT_BASE_URL': 'http://agent:8000', 'INTERNAL_SERVICE_TOKEN_FILE': '/run/internal/internal_service_token',
        'REDIS_SSL_CA_CERTS': '/run/certs/redis-ca.pem', 'DEPLOYMENT_ID': 'deepresearch',
        'PLATFORM_OPERATOR_SUBJECTS': '', 'PLATFORM_ACTIVE_RUN_LIMIT': '5', 'MAX_QUEUED_RUNS': '20',
        'WORKSPACE_CONCURRENT_RUN_LIMIT': '2', 'ALLOW_INTERNAL_MODEL_PROCESSING': 'false',
    })
    add('ConfigMap', 'deepresearch-web', data={'OIDC_CONNECT_ORIGINS': 'https://REPLACE_IDENTITY_ORIGIN',
                                            'NGINX_DNS_RESOLVER': 'REPLACE_CLUSTER_DNS_IP'})
    common_volumes = [
        {'name': 'tmp', 'emptyDir': {'sizeLimit': '256Mi'}},
        {'name': 'data', 'emptyDir': {'sizeLimit': '32Mi'}},
        {'name': 'internal', 'secret': {'secretName': 'deepresearch-internal', 'defaultMode': 292}},
        {'name': 'certificates', 'secret': {'secretName': 'deepresearch-trust', 'defaultMode': 292}},
    ]
    mounts = [{'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'data', 'mountPath': '/data'},
              {'name': 'internal', 'mountPath': '/run/internal', 'readOnly': True},
              {'name': 'certificates', 'mountPath': '/run/certs', 'readOnly': True}]
    for name in ('agent', 'worker', 'document-worker', 'backend', 'web'):
        python = name in {'agent', 'worker', 'document-worker'}
        worker = name in {'worker', 'document-worker'}
        config = 'python' if python else 'java' if name == 'backend' else 'web'
        image = 'python' if python else 'java' if name == 'backend' else 'web'
        container = {'name': name, 'image': f'deepresearch-{image}:REPLACE_IMMUTABLE_TAG',
                     'envFrom': [{'configMapRef': {'name': 'deepresearch-' + config}}],
                     'resources': {'requests': {'cpu': '250m', 'memory': '256Mi'},
                                   'limits': {'cpu': '2', 'memory': '2Gi' if python else '1Gi'}},
                     'securityContext': {'allowPrivilegeEscalation': False,
                                         'capabilities': {'drop': ['ALL']}, 'readOnlyRootFilesystem': True},
                     'volumeMounts': mounts}
        if name != 'web':
            container['envFrom'].append({'secretRef': {'name': 'deepresearch-' + config}})
        if worker:
            container['ports'] = [{'name': 'metrics', 'containerPort': 8001}]
            container['command'] = ['python', '-m', 'backend.worker', 'research' if name == 'worker' else 'documents']
            container['readinessProbe'] = {'exec': {'command': ['python', '-m', 'backend.core.worker_health', '--ready']},
                                           'periodSeconds': 10, 'timeoutSeconds': 5}
            container['livenessProbe'] = {'exec': {'command': ['python', '-m', 'backend.core.worker_health', '--live']},
                                          'periodSeconds': 20, 'timeoutSeconds': 5, 'failureThreshold': 6}
            container['lifecycle'] = {'preStop': {'exec': {'command': ['python', '-m', 'backend.core.worker_health', '--drain']}}}
        else:
            port = 8000 if python else 8080 if name == 'backend' else 80
            container['ports'] = [{'name': 'http', 'containerPort': port}]
            for probe, path in [('readinessProbe', '/readyz'), ('livenessProbe', '/nginx-livez' if name == 'web' else '/livez')]:
                container[probe] = {'httpGet': {'path': path, 'port': 'http'}, 'periodSeconds': 10,
                                    'timeoutSeconds': 5, 'failureThreshold': 6}
            add('Service', name, spec={'selector': {'app': name}, 'ports': [{'name': 'http', 'port': port, 'targetPort': 'http'}]})
        container['startupProbe'] = {**container['readinessProbe'], 'failureThreshold': 60}
        pod = {'serviceAccountName': 'deepresearch-' + config, 'automountServiceAccountToken': False,
               'terminationGracePeriodSeconds': 45, 'containers': [container], 'volumes': common_volumes,
               'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
               'topologySpreadConstraints': [{'maxSkew': 1, 'topologyKey': 'kubernetes.io/hostname',
                    'whenUnsatisfiable': 'ScheduleAnyway', 'labelSelector': {'matchLabels': {'app': name}}}]}
        if name == 'web':
            # Official nginx entrypoint renders templates and prepares its caches.
            container['securityContext']['capabilities']['add'] = ['CHOWN', 'SETUID', 'SETGID', 'NET_BIND_SERVICE']
            container['volumeMounts'] = [{'name': 'tmp', 'mountPath': '/tmp'},
                {'name': 'nginx-config', 'mountPath': '/etc/nginx/conf.d'},
                {'name': 'nginx-cache', 'mountPath': '/var/cache/nginx'}, {'name': 'nginx-run', 'mountPath': '/var/run'}]
            pod['volumes'] = [{'name': key, 'emptyDir': {'sizeLimit': '32Mi'}} for key in ('tmp', 'nginx-config', 'nginx-cache', 'nginx-run')]
        else:
            pod['securityContext'].update(runAsNonRoot=True, fsGroup=10001)
            pod['securityContext']['runAsUser'] = 10001 if python else 1000
        add('Deployment', name, version='apps/v1', spec={'replicas': 2, 'revisionHistoryLimit': 3,
            'progressDeadlineSeconds': 600, 'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}},
            'selector': {'matchLabels': {'app': name}}, 'template': {'metadata': {'labels': {'app': name}}, 'spec': pod}})
        add('PodDisruptionBudget', name, version='policy/v1', spec={'minAvailable': 1, 'selector': {'matchLabels': {'app': name}}})
    add('Job', 'deepresearch-migrate', version='batch/v1', spec={'backoffLimit': 0, 'activeDeadlineSeconds': 1200,
        'template': {'metadata': {'labels': {'app': 'migrate'}}, 'spec': {
            'serviceAccountName': 'deepresearch-migrator', 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
            'securityContext': {'runAsUser': 10001, 'runAsNonRoot': True, 'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'migrate', 'image': 'deepresearch-python:REPLACE_IMMUTABLE_TAG',
                'command': ['python', 'scripts/migrate_services.py'],
                'envFrom': [{'secretRef': {'name': 'deepresearch-migrator'}}],
                'resources': {'requests': {'cpu': '250m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}, 'readOnlyRootFilesystem': True},
                'volumeMounts': [mounts[0], mounts[1], mounts[3]]}],
            'volumes': [common_volumes[0], common_volumes[1], common_volumes[3]]}}})
    add('Ingress', 'deepresearch', version='networking.k8s.io/v1', spec={
        'ingressClassName': 'REPLACE_INGRESS_CLASS',
        'tls': [{'hosts': ['research.example.invalid'], 'secretName': 'deepresearch-ingress-tls'}],
        'rules': [{'host': 'research.example.invalid', 'http': {'paths': [{'path': '/', 'pathType': 'Prefix',
            'backend': {'service': {'name': 'web', 'port': {'number': 80}}}}]}}]})
    # Database/queue/vector/scanner/model access is outside this namespace. Replace
    # the egress destination CIDRs in the environment overlay if enforced by CNI.
    add('NetworkPolicy', 'application-ingress', version='networking.k8s.io/v1', spec={
        'podSelector': {}, 'policyTypes': ['Ingress'], 'ingress': []})
    for name, port, callers in [('web', 80, None), ('backend', 8080, ['web']), ('agent', 8000, ['backend'])]:
        rule = {'ports': [{'protocol': 'TCP', 'port': port}]}
        rule['from'] = ([{'namespaceSelector': {'matchLabels': {'deepresearch-ingress': 'true'}}}] if callers is None
                        else [{'podSelector': {'matchLabels': {'app': caller}}} for caller in callers])
        add('NetworkPolicy', name + '-ingress', version='networking.k8s.io/v1', spec={
            'podSelector': {'matchLabels': {'app': name}}, 'policyTypes': ['Ingress'], 'ingress': [rule]})
    add('NetworkPolicy', 'private-metrics', version='networking.k8s.io/v1', spec={
        'podSelector': {'matchExpressions': [{'key': 'app', 'operator': 'In',
            'values': ['backend', 'agent', 'worker', 'document-worker']}]}, 'policyTypes': ['Ingress'], 'ingress': [{
            'from': [{'namespaceSelector': {'matchLabels': {'deepresearch-monitoring': 'true'}},
                      'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'prometheus'}}}],
            'ports': [{'protocol': 'TCP', 'port': port} for port in (8000, 8001, 8080)]}]})
    return {'apiVersion': 'v1', 'kind': 'List', 'items': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    all_resources = resources()
    job = next(item for item in all_resources['items'] if item['kind'] == 'Job')
    all_resources['items'].remove(job)
    accounts = [item for item in all_resources['items'] if item['kind'] == 'ServiceAccount']
    all_resources['items'] = [item for item in all_resources['items'] if item['kind'] != 'ServiceAccount']
    bootstrap = {'apiVersion': 'v1', 'kind': 'List', 'items': accounts}
    for path, content in [(OUTPUT, all_resources), (OUTPUT.with_name('migration-job.json'), job),
                          (OUTPUT.with_name('bootstrap-resources.json'), bootstrap)]:
        output = json.dumps(content, ensure_ascii=False, indent=2) + '\n'
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != output:
                raise SystemExit('Kubernetes resources are stale: run python scripts/render_kubernetes.py')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding='utf-8')


if __name__ == '__main__':
    main()
