import io
import os
from unittest.mock import Mock, patch

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest
from pydantic import ValidationError

from backend.core.config import Settings
from backend.core.queue import redis_settings
from backend.infrastructure.object_store import ObjectStore
from backend.infrastructure.vectors import VectorIndex
from scripts.migrate_services import runtime_dsn


def s3_client():
    return boto3.client('s3', region_name='ap-southeast-1',
                        aws_access_key_id='fixture-access', aws_secret_access_key='fixture-secret')


async def test_managed_bucket_uses_default_credential_chain_and_does_not_create(settings):
    settings.object_store_backend = 's3'
    settings.object_store_auto_create_bucket = False
    settings.object_store_region = 'ap-southeast-1'
    settings.object_store_addressing_style = 'virtual'
    client = s3_client()
    with Stubber(client) as stub, patch('boto3.client', return_value=client) as factory:
        stub.add_response('head_bucket', {}, {'Bucket': settings.object_store_bucket})
        await ObjectStore(settings).start()
        options = factory.call_args.kwargs
        assert options['endpoint_url'] is None and options['region_name'] == 'ap-southeast-1'
        assert options['config'].s3 == {'addressing_style': 'virtual'}
        assert not {'aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'} & options.keys()
        stub.assert_no_pending_responses()


async def test_managed_bucket_missing_fails_instead_of_requiring_create_permission(settings):
    settings.object_store_backend = 's3'
    settings.object_store_auto_create_bucket = False
    client = s3_client()
    with Stubber(client) as stub, patch('boto3.client', return_value=client):
        stub.add_client_error('head_bucket', '404', http_status_code=404)
        with pytest.raises(RuntimeError, match='storage is unavailable'):
            await ObjectStore(settings).start()
        stub.assert_no_pending_responses()


async def test_non_default_region_creation_supplies_location_constraint(settings):
    settings.object_store_backend = 's3'
    settings.object_store_region = 'ap-southeast-1'
    client = s3_client()
    with Stubber(client) as stub, patch('boto3.client', return_value=client):
        stub.add_client_error('head_bucket', '404', http_status_code=404)
        stub.add_response('create_bucket', {}, {'Bucket': settings.object_store_bucket,
                          'CreateBucketConfiguration': {'LocationConstraint': 'ap-southeast-1'}})
        await ObjectStore(settings).start()
        stub.assert_no_pending_responses()


async def test_s3_crud_preserves_kms_encryption_and_closes_download_stream(settings):
    settings.object_store_backend = 's3'
    settings.object_store_sse = 'aws:kms'
    settings.object_store_kms_key_id = 'fixture-kms-key'
    client = s3_client()
    store = ObjectStore(settings)
    store._client = client
    bucket = settings.object_store_bucket
    encryption = {'ServerSideEncryption': 'aws:kms', 'SSEKMSKeyId': 'fixture-kms-key'}
    body = io.BytesIO(b'research')
    with Stubber(client) as stub:
        stub.add_response('put_object', {}, {'Bucket': bucket, 'Key': 'quarantine/doc', 'Body': b'research', **encryption})
        stub.add_response('get_object', {'Body': StreamingBody(body, 8)}, {'Bucket': bucket, 'Key': 'quarantine/doc'})
        stub.add_response('copy_object', {}, {'Bucket': bucket, 'Key': 'uploads/doc',
                          'CopySource': {'Bucket': bucket, 'Key': 'quarantine/doc'}, **encryption})
        stub.add_response('delete_object', {}, {'Bucket': bucket, 'Key': 'quarantine/doc'})
        stub.add_response('head_object', {}, {'Bucket': bucket, 'Key': 'uploads/doc'})
        stub.add_response('delete_object', {}, {'Bucket': bucket, 'Key': 'uploads/doc'})
        await store.put('quarantine', 'doc', b'research')
        assert await store.get('quarantine', 'doc') == b'research'
        assert body.closed
        await store.move('quarantine', 'uploads', 'doc')
        assert await store.exists('uploads', 'doc')
        await store.delete('uploads', 'doc')
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('values', [
    {'object_store_access_key': 'key'}, {'object_store_secret_key': 'secret'},
    {'object_store_session_token': 'session'}, {'object_store_kms_key_id': 'key'},
    {'object_store_endpoint': 'https://user:secret@storage.company.test'},
])
def test_storage_rejects_incomplete_credentials_and_invalid_endpoint(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, object_store_backend='s3', **values)


def test_managed_milvus_passes_authentication_database_and_private_ca(settings):
    from pydantic import SecretStr
    settings.milvus_uri = 'https://milvus.company.test'
    settings.milvus_token = SecretStr('fixture-token')
    settings.milvus_db_name = 'research'
    settings.milvus_server_pem_path = '/run/certs/milvus.pem'
    # pymilvus imports environs, which may load a developer .env on first import.
    with patch.dict(os.environ), patch('pymilvus.MilvusClient') as client:
        index = VectorIndex(settings, Mock())
        index.client()
        index.client()
        client.assert_called_once_with(uri=settings.milvus_uri, token='fixture-token', db_name='research',
                                       timeout=8, server_pem_path='/run/certs/milvus.pem')


def test_tls_redis_contract_decodes_credentials_and_verifies_hostname():
    settings = Settings(_env_file=None, redis_url='rediss://research:secret%2Bpass%2Fword@redis.company.test:6380/2',
                        redis_ssl_ca_certs='/run/certs/redis.pem')
    config = redis_settings(settings)
    assert (config.username, config.password, config.database) == ('research', 'secret+pass/word', 2)
    assert config.ssl and config.ssl_check_hostname and config.ssl_cert_reqs == 'required'
    assert config.ssl_ca_certs == '/run/certs/redis.pem'
    assert not redis_settings(Settings(_env_file=None)).ssl


def test_migrator_driver_prefix_does_not_corrupt_tls_or_password():
    dsn = 'postgresql+psycopg://dr_migrator:secret%2Bpass@postgres.company.test/research?sslmode=verify-full'
    assert runtime_dsn(dsn) == dsn.replace('postgresql+psycopg://', 'postgresql://')
    assert runtime_dsn('postgresql+asyncpg://host/db') == 'postgresql://host/db'
