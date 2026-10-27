"""Exercise scoped S3 credentials against a labelled disposable MinIO fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def verify(container):
    if not container.startswith("dr-opt-storage-"):
        raise ValueError("Isolated storage fixture required")
    label = subprocess.check_output(['docker', 'inspect', '--format', '{{ index .Config.Labels "com.deepresearch.validation" }}', container], text=True).strip()
    if label != 'optimization-20261026':
        raise ValueError('Fixture ownership label missing')
    mapping = subprocess.check_output(['docker', 'port', container, '9000/tcp'], text=True).strip()
    if not mapping.startswith('127.0.0.1:') or '\n' in mapping:
        raise ValueError('A single local fixture port is required')
    clients = {}
    for key, secret in [('fixture-root', 'fixture-root-not-production'),
                        ('dr-objects', 'fixture-business-distinct-password'),
                        ('dr-milvus', 'fixture-milvus-distinct-password')]:
        clients[key] = boto3.client('s3', endpoint_url='http://' + mapping,
            aws_access_key_id=key, aws_secret_access_key=secret, region_name='us-east-1',
            config=Config(proxies={}, retries={'total_max_attempts': 1}))
    root, business, milvus = (clients[key] for key in ('fixture-root', 'dr-objects', 'dr-milvus'))
    data = b'synthetic-preexisting-object-not-user-data'
    root.put_object(Bucket='deepresearch-documents', Key='uploads/retained', Body=data)
    assert business.get_object(Bucket='deepresearch-documents', Key='uploads/retained')['Body'].read() == data
    business.head_bucket(Bucket='deepresearch-documents')
    business.put_object(Bucket='deepresearch-documents', Key='quarantine/new', Body=data)
    business.copy_object(Bucket='deepresearch-documents', Key='uploads/new', CopySource={'Bucket':'deepresearch-documents','Key':'quarantine/new'})
    business.delete_object(Bucket='deepresearch-documents', Key='quarantine/new')
    milvus.put_object(Bucket='a-bucket', Key='files/fixture', Body=data)
    assert milvus.get_object(Bucket='a-bucket', Key='files/fixture')['Body'].read() == data
    upload = milvus.create_multipart_upload(Bucket='a-bucket', Key='files/multipart')['UploadId']
    milvus.abort_multipart_upload(Bucket='a-bucket', Key='files/multipart', UploadId=upload)
    checks = [('business_unrelated_bucket', lambda: business.get_object(Bucket='a-bucket',Key='files/fixture')),
              ('business_unapproved_prefix', lambda: business.put_object(Bucket='deepresearch-documents',Key='outside/fixture',Body=data)),
              ('business_bucket_admin', lambda: business.create_bucket(Bucket='forbidden-fixture-bucket')),
              ('milvus_business_original', lambda: milvus.get_object(Bucket='deepresearch-documents',Key='uploads/retained'))]
    denied = []
    for name, action in checks:
        try:
            action()
        except ClientError as exc:
            assert exc.response['Error']['Code'] == 'AccessDenied'
            denied.append(name)
        else:
            raise AssertionError('Storage permission boundary failed: ' + name)
    assert {row['Name'] for row in business.list_buckets()['Buckets']} == {'deepresearch-documents'}
    assert {row['Name'] for row in milvus.list_buckets()['Buckets']} == {'a-bucket'}
    return {'passed': True, 'scope': 'labelled isolated MinIO fixture, real S3 requests, no model calls',
            'denied': denied, 'preexisting_object_sha256': hashlib.sha256(data).hexdigest(),
            'allowed': ['business_head_bucket','business_quarantine_upload','business_publish_copy','business_cleanup','milvus_object_io','milvus_multipart_abort','bucket_listing_filtered_to_authorized_bucket'],
            'server_image': subprocess.check_output(['docker','inspect','--format','{{.Image}}',container],text=True).strip()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--container', required=True)
    parser.add_argument('--output', default='docs/evidence/optimization-20261026/storage-isolated.json')
    args = parser.parse_args()
    report = verify(args.container)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
