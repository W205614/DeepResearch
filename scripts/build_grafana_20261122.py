"""Build Grafana 13.2.6 with the thirteen verified bundled backends."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261114 as retained  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402
from scripts.prefetch_minio_verified_20261115 import fetch_url  # noqa: E402
from scripts.go_stdlib_rebuild_20261112 import require  # noqa: E402

IMAGE = 'deepresearch-grafana-source:20261122'
PROFILE = 'grafana-source-20261122'
DOCKERFILE = 'deployment/docker/Dockerfile.grafana-20261122'
VERSION = '13.2.6'
COMMIT = '45abbd2943cd6767cca037a5c554a6ec9c3fbf5e'
SOURCE = {
    'name': 'source.tar.gz',
    'url': 'https://codeload.github.com/grafana/grafana/tar.gz/refs/tags/v13.2.6',
    'bytes': 49065463,
    'sha256': '619c2c6e37ec80dd8fa0bcb4452e06161adc76da211413805f5f9210a058623a',
}
MODULES = {
    'github.com/!azure/azure-sdk-for-go': 'v68.0.0+incompatible',
    'github.com/blevesearch/geo': 'v0.2.5',
    'github.com/olekukonko/ll': 'v0.1.8',
    'github.com/prometheus/prometheus': 'v0.313.0',
}
MAIN = 'usr/share/grafana/bin/grafana'
MAIN_SHA256 = '765e65defcafac89376a8069a5ace18cdf75d332916a158e3d37be045d9f81ba'
EVIDENCE_PATH = 'usr/share/deepresearch/grafana-13.2.6-candidate/'
EMPTY_LAYER = 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef'


def fetch_module(context, item):
    module, version = item
    path = context / module / '@v'
    path.mkdir(parents=True)
    for ext in ('.info', '.mod', '.zip'):
        name = version + ext
        url = 'https://proxy.golang.org/' + quote(module, safe='/') + '/@v/' + name
        subprocess.run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                        '--retry', '2', '--retry-all-errors', '--max-time', '180',
                        '--silent', '--show-error', url, '--output', str(path / name)], check=True)


def prepare(output, source_archive, module_transport):
    directory = output / 'source-input'
    directory.mkdir()
    path = directory / SOURCE['name']
    if source_archive is not None:
        shutil.copyfile(source_archive, path)
    else:
        evidence = output / 'source-receipts'
        evidence.mkdir()
        fetch_url(SOURCE, SOURCE['url'], path, evidence)
    require(path.stat().st_size == SOURCE['bytes'] and retained.digest(path) == SOURCE['sha256'],
            'The complete official Grafana source archive changed')
    if module_transport is None:
        module_transport = output / 'module-transport'
        module_transport.mkdir()
        # The original go.sum and readonly build verify these files after transport.
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda item: fetch_module(module_transport, item), MODULES.items()))
    return directory, module_transport


def verify_image(output):
    facts = capture(IMAGE, output / 'actual-payload', save_config=True)
    inspection = facts['inspection']
    config = inspection['Config']
    rows = {row['path']: row for row in facts['runtime_manifest']}
    require(facts['diff_ids'][1:] in ([], [EMPTY_LAYER]) and facts['diff_ids'][0] != EMPTY_LAYER
            and inspection['Architecture'] == 'amd64' and inspection['Os'] == 'linux'
            and config['Entrypoint'] == ['/run.sh'] and config['User'] == '472'
            and config['WorkingDir'] == '/usr/share/grafana' and set(config['ExposedPorts']) == {'3000/tcp'}
            and config['Labels']['org.opencontainers.image.version'] == VERSION + '-source-maintenance-20261122'
            and config['Labels']['org.deepresearch.upstream.grafana_revision'] == COMMIT
            and config['Labels']['org.deepresearch.binary-redistribution-approved'] == 'false',
            'Grafana platform, runtime contract or source revision differs')
    profile = retained.load_profile()
    plugins = {name: digest for name, digest in profile['expected']['native_sha256'].items() if name != MAIN}
    require(len(plugins) == 13 and all(rows[name]['sha256'] == digest for name, digest in plugins.items()),
            'One of the thirteen retained Grafana backends differs')
    require(rows[MAIN]['sha256'] == MAIN_SHA256
            and not any(row['type'] != 'directory' and Path(name).name in {'libssl.so.3', 'libcrypto.so.3', 'apk'}
                        for name, row in rows.items()),
            'The older Grafana main executable or removed native packages returned')
    receipt = {key: facts[key] for key in ('image_id', 'config_digest', 'diff_ids', 'runtime_manifest_sha256')}
    receipt.update(profile=PROFILE, image=IMAGE, native_sha256={MAIN: rows[MAIN]['sha256'], **plugins},
                   runtime_config=retained.runtime_config(config), binary_redistribution_approved=False)
    for name, value in (('build-receipt.json', receipt), ('image-inspect.json', inspection)):
        (output / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.cache/eval' / ('grafana-1326-' + uuid.uuid4().hex))
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--module-transport', type=Path)
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    if not args.no_build:
        source, modules = prepare(args.output_dir, args.source_archive, args.module_transport)
        subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '--file', DOCKERFILE,
                        '--build-context', f'grafana-source={source.resolve()}',
                        '--build-context', f'grafana-module-transport={modules.resolve()}',
                        '--tag', IMAGE, '.'], cwd=ROOT, check=True)
    result = verify_image(args.output_dir)
    print(json.dumps({'profile': PROFILE, 'image_id': result['image_id'], 'config_digest': result['config_digest']}))


if __name__ == '__main__':
    main()
