from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from backend.infrastructure.object_store import ObjectStore


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
