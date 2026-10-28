"""Transport an unchanged ancestor candidate; this is never runtime approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'ee370ae7841f7ae7cb85f7705d07dfb7ddd20d5e'
ARTIFACT = 12811205583
ARTIFACT_SHA256 = '54d72411514ffa3f0aa24e35aa8b2fbc7d47e28191819831da51f84352e51457'
REPOSITORY = 'W205614/DeepResearch'
MAX_BYTES = 2 * 1024**3


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, stderr=subprocess.PIPE)


def compile_input(name):
    return (name in {'.dockerignore', 'deployment/docker/Dockerfile.grafana-security',
                     'deployment/vendors/grafana-security/source.lock.json',
                     'deployment/vendors/grafana-security/build_candidate_backends.py',
                     'deployment/vendors/grafana-security/candidate_build_backends.py',
                     'deployment/vendors/grafana-security/capture_graphs.py'}
            or name.startswith(('deployment/vendors/grafana/',
                                'deployment/vendors/grafana-security/main/',
                                'deployment/vendors/grafana-security/plugins/')))


def source_identity():
    head = git('rev-parse', 'HEAD').decode().strip()
    git('merge-base', '--is-ancestor', SOURCE, head)
    if git('status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Current tracked source is dirty')
    inventories = []
    for revision in (SOURCE, head):
        names = git('ls-tree', '-r', '--name-only', '-z', revision).decode().split('\0')
        inventories.append({name for name in names if name and compile_input(name)})
    if not inventories[0] or inventories[0] != inventories[1]:
        raise ValueError('Candidate compile-input inventory changed; a fresh build is required')
    hashes = {}
    for name in sorted(inventories[0]):
        expected = git('show', SOURCE + ':' + name)
        if expected != git('show', head + ':' + name) or expected != (ROOT / name).read_bytes():
            raise ValueError('Candidate compile-input bytes changed; a fresh build is required')
        hashes[name] = hashlib.sha256(expected).hexdigest()
    return {'built_from_application_commit': SOURCE, 'consumed_by_application_commit': head,
            'unchanged_compile_inputs': hashes, 'artifacts_rebuilt_at_current_commit': False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(output):
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        raise ValueError('Read-only Actions token is required')
    base = f'https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{ARTIFACT}'
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'User-Agent': 'DeepResearch-candidate-material-transport'}
    with urllib.request.urlopen(urllib.request.Request(base, headers=headers), timeout=60) as response:
        metadata = json.load(response)
    if (metadata.get('id') != ARTIFACT or metadata.get('expired') is not False
            or metadata.get('workflow_run', {}).get('head_sha') != SOURCE
            or metadata.get('name') != 'grafana-maintenance-candidate-' + SOURCE
            or metadata.get('digest') != 'sha256:' + ARTIFACT_SHA256
            or not 0 < metadata.get('size_in_bytes', 0) <= MAX_BYTES):
        raise ValueError('Immutable ancestor artifact identity, digest or retention differs')
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(urllib.request.Request(base + '/zip', headers=headers), timeout=60)
    except urllib.error.HTTPError as response:
        if response.code != 302:
            raise
        location = response.headers.get('Location', '')
    else:
        raise ValueError('Expected a signed artifact-storage redirect')
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Artifact storage redirect is not a verified HTTPS URL')
    # Do not forward the repository token to artifact storage.
    path, count = output / 'ancestor-artifact.zip', 0
    with urllib.request.urlopen(location, timeout=120) as response, path.open('xb') as stream:
        while block := response.read(8 * 1024 * 1024):
            count += len(block)
            if count > MAX_BYTES:
                raise ValueError('Ancestor artifact exceeds the transport bound')
            stream.write(block)
    if count != metadata['size_in_bytes'] or digest(path) != ARTIFACT_SHA256:
        raise ValueError('Complete ancestor ZIP digest differs')
    extracted, seen, total = output / 'extracted', set(), 0
    extracted.mkdir()
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            relative = PurePosixPath(item.filename)
            if (relative.is_absolute() or '..' in relative.parts or '\\' in item.filename
                    or item.filename in seen or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe or duplicate ancestor ZIP member')
            seen.add(item.filename)
            target = extracted / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += item.file_size
            if item.file_size < 0 or total > MAX_BYTES:
                raise ValueError('Expanded ancestor artifact exceeds its bound')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination, 8 * 1024 * 1024)
    archives = list(extracted.rglob('grafana-candidate.oci.tar'))
    records = list(extracted.rglob('candidate-materials.json'))
    if len(archives) != 1 or len(records) != 1:
        raise ValueError('Complete ancestor OCI and material record are required')
    report = json.loads(records[0].read_bytes())
    if (report.get('operational_success') is not True or report.get('artifact_approved') is not False
            or report.get('security_gate_claim') is not False
            or digest(archives[0]) != report.get('archive_sha256')):
        raise ValueError('Ancestor material record or full OCI bytes differ')
    return archives[0], metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists() or not args.output_dir.resolve().is_relative_to((ROOT / '.cache').resolve()):
        raise ValueError('Use a new candidate transport directory below this checkout .cache')
    args.output_dir.mkdir(parents=True)
    record = {'reused': False, 'artifact_approved': False, 'security_gate_claim': False}
    try:
        record['source_identity'] = source_identity()
        archive, metadata = download(args.output_dir)
        destination = ROOT / '.cache/eval/grafana-candidate.oci.tar'
        if destination.exists():
            raise ValueError('Candidate archive destination already exists')
        archive.rename(destination)
        record.update(reused=True, artifact_id=ARTIFACT, ancestor_artifact_sha256=ARTIFACT_SHA256,
                      archive_sha256=digest(destination), archive_bytes=destination.stat().st_size,
                      archive_workflow_run=metadata['workflow_run'])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        record.update(reuse_error_type=type(error).__name__,
                      boundary='No candidate reuse claim. The unchanged security workflow must build a fresh candidate.')
    (args.output_dir / 'reuse.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write('reused=' + str(record['reused']).lower() + '\n')
    print(json.dumps({'reused': record['reused'], 'artifact_approved': False, 'security_gate_claim': False}))


if __name__ == '__main__':
    main()
