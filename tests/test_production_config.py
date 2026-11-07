from copy import deepcopy
import json

import pytest

from scripts.check_production_config import inspect_configuration


@pytest.fixture
def cloud_config():
    python = {
        'AUTH_MODE': 'oidc', 'DEMO_MODE': 'false', 'QUEUE_BACKEND': 'redis', 'DOCUMENT_SCAN_MODE': 'clamav',
        'OBJECT_STORE_BACKEND': 's3', 'CHECKPOINT_SCHEMA_MANAGED': 'true',
        'OIDC_ISSUER': 'https://identity.company.internal/tenant', 'OIDC_AUDIENCE': 'research-api',
        'OIDC_JWKS_URL': 'https://identity.company.internal/keys',
        'DATABASE_URL': 'postgresql://dr_python:private-python-password@pg.company.internal/research?sslmode=verify-full&sslrootcert=/run/certs/postgres-ca.pem',
        'REDIS_URL': 'rediss://queue:private-queue-password@redis.company.internal/0',
        'MILVUS_URI': 'https://milvus.company.internal', 'MILVUS_TOKEN': 'private-vector-token',
        'OBJECT_STORE_BUCKET': 'research-documents', 'OBJECT_STORE_REGION': 'ap-southeast-1',
        'OBJECT_STORE_AUTO_CREATE_BUCKET': 'false', 'CLAMAV_HOST': 'scanner.company.internal',
        'LLM_PROVIDER': 'compatible', 'LLM_BASE_URL': 'https://models.company.internal/v1', 'LLM_MODEL_ID': 'research-model',
        'LLM_API_KEY': 'private-llm-key', 'EMBEDDING_BASE_URL': 'https://embedding.company.internal/v1',
        'EMBEDDING_MODEL': 'research-embedding', 'EMBEDDING_API_KEY': 'private-embedding-key',
        'WEB_SEARCH_PROVIDER': 'bocha', 'BOCHA_API_KEY': 'private-search-key',
        'BOCHA_BASE_URL': 'https://search.company.internal/v1', 'RERANKER_ENABLED': 'false',
        'MAX_QUEUED_RUNS': '20', 'WORKSPACE_CONCURRENT_RUN_LIMIT': '2', 'PLATFORM_ACTIVE_RUN_LIMIT': '5',
        'PLATFORM_OPERATOR_SUBJECTS': 'operator',
    }
    java = {key: python[key] for key in ('AUTH_MODE', 'OIDC_ISSUER', 'OIDC_AUDIENCE', 'OIDC_JWKS_URL', 'PLATFORM_OPERATOR_SUBJECTS')}
    java.update(SPRING_DATASOURCE_URL='jdbc:postgresql://pg.company.internal/research?sslmode=verify-full&sslrootcert=/run/certs/postgres-ca.pem',
                SPRING_DATASOURCE_USERNAME='dr_java', SPRING_DATASOURCE_PASSWORD='private-java-password',
                REDIS_URL=python['REDIS_URL'], DEPLOYMENT_ID='acceptance', MAX_QUEUED_RUNS='20',
                WORKSPACE_CONCURRENT_RUN_LIMIT='2', PLATFORM_ACTIVE_RUN_LIMIT='5')
    java['SPRING_APPLICATION_JSON'] = json.dumps({'management': {'metrics': {
        'distribution': {'percentiles-histogram': {'http.server.requests': True}}}}})
    config = {'x-public-url': 'https://research.company.internal', 'services': {
        'agent': {'environment': deepcopy(python)}, 'worker': {'environment': deepcopy(python)},
        'document-worker': {'environment': deepcopy(python)}, 'backend': {'environment': java},
        'migrate': {'environment': {'DATABASE_URL': 'postgresql+psycopg://dr_migrator:private-migrator-password@pg.company.internal/research?sslmode=verify-full&sslrootcert=/run/certs/postgres-ca.pem'}},
        'token-init': {}, 'agent-gateway': {},
        'web': {'environment': {'OIDC_CONNECT_ORIGINS': 'https://identity.company.internal'}},
        'edge': {'ports': [{'host_ip': '127.0.0.1', 'published': '8080', 'target': 8080}]},
    }}
    for name in ('web', 'backend', 'agent', 'worker', 'document-worker'):
        config['services'][name]['deploy'] = {'replicas': 2}
    for name in ('backend', 'agent', 'worker', 'document-worker', 'migrate'):
        config['services'][name]['volumes'] = [{'type': 'bind', 'source': '/fixture/ca', 'target': '/run/certs', 'read_only': True}]
    for name, role in (('worker', 'research'), ('document-worker', 'documents')):
        config['services'][name]['command'] = ['python', '-m', 'backend.worker', role]
        config['services'][name]['healthcheck'] = {'test': ['CMD', 'python', '-m', 'backend.core.worker_health', '--ready']}
    return config


def test_config_check_is_offline_and_never_promises_live_acceptance_or_prints_secrets(cloud_config):
    report = inspect_configuration(cloud_config)
    assert report['configuration_valid']
    assert not report['production_approved']
    assert report['live_connectivity'] == 'not_checked'
    output = json.dumps(report)
    assert 'private-' not in output
    assert 'company.internal' not in output


@pytest.mark.parametrize('service,key,value', [
    ('worker', 'DEMO_MODE', 'true'), ('document-worker', 'DOCUMENT_SCAN_MODE', 'disabled'),
    ('worker', 'OIDC_ISSUER', 'https://another.company.internal'),
    ('agent', 'REDIS_URL', 'redis://queue:password@redis.company.internal/0'),
    ('agent', 'DATABASE_URL', 'postgresql://dr_python:password@pg.company.internal/research?sslmode=require'),
    ('agent', 'MILVUS_TOKEN', ''), ('agent', 'OBJECT_STORE_AUTO_CREATE_BUCKET', 'true'),
    ('agent', 'OBJECT_STORE_ACCESS_KEY', 'incomplete'), ('agent', 'OBJECT_STORE_SESSION_TOKEN', 'incomplete'),
    ('agent', 'MIGRATOR_DATABASE_URL', 'private-migrator-dsn'),
    ('agent', 'OTEL_EXPORTER_OTLP_ENDPOINT', 'http://collector.company.internal:4317'),
    ('web', 'OIDC_CONNECT_ORIGINS', 'https://*'),
    ('backend', 'SPRING_DATASOURCE_USERNAME', 'postgres'),
    ('backend', 'OIDC_JWKS_URL', 'https://wrong.company.internal/keys'),
    ('backend', 'SPRING_APPLICATION_JSON', '{}'), ('agent', 'OIDC_CA_CERTS', '/run/certs/oidc-ca.pem'),
    ('worker', 'MILVUS_SERVER_PEM_PATH', '/unmounted/milvus-ca.pem'),
    ('backend', 'PLATFORM_OPERATOR_SUBJECTS', ''),
    ('agent', 'LLM_PROVIDER', 'deepseek'),
])
def test_preflight_rejects_miswired_or_insecure_cloud_configuration(cloud_config, service, key, value):
    cloud_config['services'][service]['environment'][key] = value
    report = inspect_configuration(cloud_config)
    assert not report['configuration_valid']
    assert any(not check['passed'] for check in report['checks'])
    assert 'private-' not in json.dumps(report)


def test_preflight_rejects_public_backend_port(cloud_config):
    cloud_config['services']['backend']['ports'] = [{'target': 8080, 'host_ip': '0.0.0.0'}]
    report = inspect_configuration(cloud_config)
    assert not next(check for check in report['checks'] if check['check'] == 'private_ports')['passed']


def test_missing_ca_mount_and_matching_private_oidc_truststore_are_checked(cloud_config):
    cloud_config['services']['worker']['volumes'] = []
    assert not inspect_configuration(cloud_config)['configuration_valid']
    cloud_config['services']['worker']['volumes'] = cloud_config['services']['agent']['volumes']
    for name in ('agent', 'worker', 'document-worker'):
        cloud_config['services'][name]['environment']['OIDC_CA_CERTS'] = '/run/certs/oidc-ca.pem'
    cloud_config['services']['backend']['environment']['JAVA_TOOL_OPTIONS'] = '-Djavax.net.ssl.trustStore=/run/certs/oidc-truststore.p12'
    assert inspect_configuration(cloud_config)['configuration_valid']


def test_legacy_embeddings_require_explicit_exception_and_never_reuse_new_openai_key(cloud_config):
    agent = cloud_config['services']['agent']['environment']
    for name in ('agent', 'worker', 'document-worker'):
        cloud_config['services'][name]['environment'].update(LLM_PROVIDER='openai', LLM_BASE_URL='https://api.openai.com/v1')
    assert not inspect_configuration(cloud_config)['configuration_valid']
    assert inspect_configuration(cloud_config, allow_legacy_embedding_endpoint=True)['configuration_valid']
    agent['EMBEDDING_API_KEY'] = agent['LLM_API_KEY']
    assert not inspect_configuration(cloud_config, allow_legacy_embedding_endpoint=True)['configuration_valid']
    for name in ('agent', 'worker', 'document-worker'):
        cloud_config['services'][name]['environment']['EMBEDDING_BASE_URL'] = 'https://api.openai.com/v1'
    assert inspect_configuration(cloud_config)['configuration_valid']
