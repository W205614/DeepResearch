"""Verify a public Docker Hub index -> amd64 manifest -> config/layer hierarchy.

Anonymous registry bearer tokens are kept only in memory. No containers start.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def inspect(reference):
    if '@' in reference:
        repository, tag = reference.split('@', 1)
    else:
        repository, tag = reference.rsplit(':', 1)
    if not re.fullmatch(r'[a-z0-9][a-z0-9._/-]*', repository):
        raise ValueError('Explicit public Docker Hub repository required')
    if '/' not in repository:
        repository = 'library/' + repository
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', tag):
        raise ValueError('Explicit version/digest required')
    with urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:' + repository + ':pull', timeout=30) as response:
        token = json.load(response)['token']
    accept = ', '.join(['application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json',
                        'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'])
    headers = {'Authorization': 'Bearer ' + token, 'Accept': accept}

    def get(kind, value, expected=None):
        request = urllib.request.Request('https://registry-1.docker.io/v2/' + repository + '/' + kind + '/' + value, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            stated = response.headers.get('Docker-Content-Digest')
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Registry metadata exceeds bound')
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        if (stated and stated != digest) or (expected and expected != digest):
            raise ValueError('Registry metadata digest mismatch')
        return json.loads(raw), digest

    index, index_digest = get('manifests', tag)
    if 'manifests' in index:
        descriptor = next(item for item in index['manifests'] if item.get('platform', {}).get('os') == 'linux' and item.get('platform', {}).get('architecture') == 'amd64')
        manifest, digest = get('manifests', descriptor['digest'], descriptor['digest'])
    else:
        manifest, digest = index, index_digest
    configuration, _ = get('blobs', manifest['config']['digest'], manifest['config']['digest'])
    if configuration.get('architecture') != 'amd64' or configuration.get('os') != 'linux':
        raise ValueError('Unexpected platform')
    return {'reference': reference, 'index_digest': index_digest, 'platform': 'linux/amd64', 'platform_manifest': digest,
            'config_digest': manifest['config']['digest'], 'layers': manifest['layers'],
            'compressed_layer_bytes': sum(layer['size'] for layer in manifest['layers']),
            'pull_reference': repository + '@' + digest, 'created': configuration.get('created'),
            'runtime_user': configuration.get('config', {}).get('User', ''), 'metadata_digests_verified': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = inspect(args.image)
    with Path(args.output).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2))
    print(json.dumps(report))
