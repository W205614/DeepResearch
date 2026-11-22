"""Build the Alpine MinIO runtime and verify the actual retained executables."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_backend_busybox_20261113 as busybox  # noqa: E402
from scripts import check_minio_otel_20261115 as minio  # noqa: E402
from scripts import prefetch_minio_verified_20261115 as transport  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402

IMAGE = 'deepresearch-minio-alpine:20261121'
PROFILE = 'minio-alpine-source-20261121'
DOCKERFILE = 'deployment/docker/Dockerfile.minio-alpine-20261121'
ENTRYPOINT_SHA256 = '77bebe7e478412a9f54b1e02fa605764b8cb4e33818ec2efe82fee6c73f61fc0'
CURL_SHA256 = 'c62ec1b00124a398682a1c246f0406ac520fc79c01e26a1b78bd3f17510f69ea'
CURL_OPENSSL_APKS = {
    'libcrypto3-3.5.9-r1.apk': (1980117, '042026082cc9bc7872d1baa81755c50efa70b05f03b5369fdc5f0a1303009720'),
    'libssl3-3.5.9-r1.apk': (382098, '18c5778cbe52e1991303f8531970b05d4e6e036ea08349322f7422f42a689dc7'),
    'openssl-dev-3.5.9-r1.apk': (365989, '6a5734c3a00cf03aaa9cb0f4ff8c6d12d3b0c0c5f1490a3491eaf13a16ae044c'),
    'openssl-libs-static-3.5.9-r1.apk': (14271920, '0bfd81f8b0623d80afd5aa8f0ced1e36c4537bc42eaf376565f703477789cbd6'),
}
GRAPH_SHA256 = {
    'silo': 'b9c14d18150cd1c6d45b2eb65d3e4dc2f7d29b18eae2bd5ab2d36b4c0fad3669',
    'mcli': '61b98438da7c3ef4fcdadb7a15faec37ecaea76bc6d3fd20ef5ad8447cf428dc',
}


def prepare_curl_inputs(output, input_dir):
    directory, evidence = output / 'curl-inputs', output / 'curl-input-receipts'
    directory.mkdir()
    evidence.mkdir()
    previous = minio.prior.read(transport.frozen.LOCK)
    rows = [row for row in previous['inputs'] if
            (row['name'].endswith('.apk') and row['name'] not in transport.FROZEN_OPENSSL_APKS)
            or row['name'] in {'curl-8.23.0.tar.xz', 'curl-8.23.0.tar.xz.asc', 'daniel-stenberg.asc'}]
    rows.extend({'name': name, 'bytes': size, 'sha256': digest,
                 'url': 'https://dl-cdn.alpinelinux.org/alpine/v3.24/main/x86_64/' + name}
                for name, (size, digest) in CURL_OPENSSL_APKS.items())
    if input_dir is not None:
        for row in rows:
            source = input_dir / row['name']
            if source.is_file():
                shutil.copyfile(source, directory / source.name)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda row: transport.fetch_one(row, directory, evidence), rows))
    transport.frozen.verify(directory, {'inputs': rows})
    return directory


def receipt(output):
    otel = minio.prior.read(ROOT / minio.PROFILE_PATH)
    busybox_profile = (ROOT / busybox.PROFILE).read_bytes()
    busybox.require(busybox.sha(busybox_profile) == busybox.RETAINED_PROFILE_SHA256,
                    'Original reviewed BusyBox proof differs')
    fixed_shell = json.loads(busybox_profile)['payload']['/bin/busybox']['sha256']
    expected = {**otel['expected']['native_sha256'],
                'usr/bin/curl': CURL_SHA256,
                'bin/busybox': fixed_shell, 'usr/bin/docker-entrypoint.sh': ENTRYPOINT_SHA256,
                'licenses/curl/COPYING': '82f2f4427d6545ee5aaac4f0b80428da6cc8ba41c2cf5da3a03680ec327b9681',
                'licenses/curl/build-packages.txt': '93b7b056acba022b6daae55b4bdedbedfb78eee6d31b5d5d5617f77c4863d464'}
    facts = capture(IMAGE, output / 'actual-payload', save_config=True)
    paths = {row['path']: row for row in facts['runtime_manifest']}
    for name, digest in expected.items():
        current = paths.get(name, {})
        busybox.require(current.get('type') == 'file' and current.get('sha256') == digest,
                        'Alpine MinIO pinned payload differs: ' + name)
    for binary, digest in GRAPH_SHA256.items():
        graph = paths.get(f'usr/share/deepresearch-build/{binary}-graph.json', {})
        busybox.require(graph.get('type') == 'file' and graph.get('sha256') == digest,
                        'MinIO retained compiled import graph differs: ' + binary)

    inspection = facts['inspection']
    config = inspection['Config']
    busybox.require(inspection['Os'] == 'linux' and inspection['Architecture'] == 'amd64'
                    and len(facts['diff_ids']) == 1
                    and config['Entrypoint'] == ['/usr/bin/docker-entrypoint.sh'] and config['Cmd'] == ['silo']
                    and set(config['Volumes']) == {'/data'} and set(config['ExposedPorts']) == {'9000/tcp', '9001/tcp'}
                    and 'lib/ld-musl-x86_64.so.1' in paths and 'var/lib/rpm/rpmdb.sqlite' not in paths,
                    'MinIO runtime launch configuration or distro boundary differs')
    for name in paths:
        leaf = Path(name).name
        busybox.require(leaf not in {'libc.so.6', 'ld-linux-x86-64.so.2', 'libcrypto.so.3', 'libssl.so.3'},
                        'MinIO runtime contains a replaced native component: ' + name)
    result = {key: facts[key] for key in ('image_id', 'config_digest', 'diff_ids', 'runtime_manifest_sha256')}
    result.update(profile=PROFILE, image=IMAGE, payload_sha256=expected,
                  runtime_config=minio.prior.shared.runtime_config(config), binary_redistribution_approved=False)
    for name, value in [('build-receipt.json', result), ('image-inspect.json', inspection)]:
        (output / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.cache/eval' / ('minio-alpine-' + uuid.uuid4().hex))
    parser.add_argument('--curl-input-dir', type=Path)
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    if not args.no_build:
        curl_inputs = prepare_curl_inputs(args.output_dir, args.curl_input_dir)
        subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '--build-context',
                        f'curl-inputs={curl_inputs.resolve()}', '--file', DOCKERFILE, '--tag', IMAGE, '.'],
                       cwd=ROOT, check=True)
    result = receipt(args.output_dir)
    print(json.dumps({'profile': PROFILE, 'image_id': result['image_id'], 'config_digest': result['config_digest']}))


if __name__ == '__main__':
    main()
