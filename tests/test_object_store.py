from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend.infrastructure.object_store import ObjectStore
from backend.core.reliability import ServiceError


def error(code, operation):
    return ClientError({'Error': {'Code': code}}, operation)


@pytest.mark.parametrize('scenario', ['existing', 'missing', 'concurrent_creation'])
async def test_bucket_start_accepts_existing_or_concurrently_created_bucket(settings, scenario):
    settings.object_store_backend = 's3'
    settings.object_store_endpoint = 'http://fixture.invalid'
    client = Mock()
    if scenario != 'existing':
        client.head_bucket.side_effect = [error('404', 'HeadBucket'), {}]
    if scenario == 'concurrent_creation':
        client.create_bucket.side_effect = error('BucketAlreadyOwnedByYou', 'CreateBucket')
    with patch('boto3.client', return_value=client):
        await ObjectStore(settings).start()
    assert client.head_bucket.call_count == (2 if scenario == 'concurrent_creation' else 1)
    assert client.create_bucket.call_count == (0 if scenario == 'existing' else 1)


@pytest.mark.parametrize('scenario', ['head_denied', 'other_owner', 'recheck_denied'])
async def test_bucket_start_does_not_hide_permission_or_ownership_errors(settings, scenario):
    settings.object_store_backend = 's3'
    settings.object_store_endpoint = 'http://fixture.invalid'
    client = Mock()
    if scenario == 'head_denied':
        client.head_bucket.side_effect = error('AccessDenied', 'HeadBucket')
    else:
        client.head_bucket.side_effect = [error('404', 'HeadBucket'), error('AccessDenied', 'HeadBucket')]
        client.create_bucket.side_effect = error(
            'BucketAlreadyExists' if scenario == 'other_owner' else 'BucketAlreadyOwnedByYou', 'CreateBucket')
    with patch('boto3.client', return_value=client), pytest.raises(RuntimeError, match='storage is unavailable'):
        await ObjectStore(settings).start()
    if scenario == 'head_denied':
        client.create_bucket.assert_not_called()


@pytest.mark.parametrize('operation,mutating', [('ping',False),('put',True),('get',False),
                                              ('move',True),('delete',True),('exists',False)])
async def test_storage_outage_is_retryable_and_never_misreported_as_missing(settings, operation, mutating):
    settings.object_store_backend = 's3'
    store = ObjectStore(settings)
    client = Mock()
    outage = EndpointConnectionError(endpoint_url='http://private-storage.invalid/secret-key')
    for method in ('head_bucket','put_object','get_object','copy_object','delete_object','head_object'):
        getattr(client, method).side_effect = outage
    store._client = client
    arguments = {'ping':(), 'put':('quarantine','doc',b'content'), 'get':('uploads','doc'),
                 'move':('quarantine','uploads','doc'), 'delete':('uploads','doc'),
                 'exists':('uploads','doc')}
    with pytest.raises(ServiceError) as raised:
        await getattr(store, operation)(*arguments[operation])
    assert raised.value.code == 'storage_unavailable'
    assert raised.value.retryable and raised.value.result_unknown is mutating
    assert 'private-storage' not in str(raised.value)
    assert 'secret-key' not in str(raised.value)
    assert raised.value.__suppress_context__


async def test_storage_missing_is_distinct_from_denied_or_unreachable(settings):
    settings.object_store_backend = 's3'
    store = ObjectStore(settings)
    store._client = Mock()
    store._client.head_object.side_effect = error('404','HeadObject')
    assert not await store.exists('uploads','doc')
    store._client.get_object.side_effect = error('NoSuchKey','GetObject')
    with pytest.raises(ServiceError) as raised:
        await store.get('uploads','doc')
    assert raised.value.code == 'storage_missing' and not raised.value.retryable
    store._client.head_object.side_effect = error('AccessDenied','HeadObject')
    with pytest.raises(ServiceError) as raised:
        await store.exists('uploads','doc')
    assert raised.value.code == 'storage_unavailable' and raised.value.retryable
