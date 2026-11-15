"""Bind independently reviewed Milvus OS findings to complete owned payloads."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import posixpath
import tarfile

from scripts import check_grafana_source_build_20261110 as shared
from scripts.go_stdlib_rebuild_20261112 import require

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'deployment/security/milvus-os-20261115-profile.json'
PROFILE_SHA256 = 'd99c0a8b11bc9a046300c8fad1e2cc862572775b21d9201a0793485ffdd9cfef'


def finding_key(row):
    return tuple(row.get(key, '') for key in ('VulnerabilityID', 'PkgName', 'InstalledVersion',
                                             'Severity', 'Status', 'FixedVersion'))


def resolve_owned_path(name, rows):
    require(name.startswith('/') and '..' not in name.split('/'), 'Invalid dpkg-owned path')
    name, links, seen = name.lstrip('/'), [], set()
    for _ in range(40):
        pieces = name.split('/')
        for index in range(1, len(pieces) + 1):
            prefix = '/'.join(pieces[:index])
            row = rows.get(prefix)
            if row is None or row['type'] not in ('symlink', 'hardlink'):
                continue
            require(prefix not in seen, 'Cyclic dpkg-owned payload link')
            seen.add(prefix)
            links.append(row)
            target = row['target']
            base = (target.lstrip('/') if target.startswith('/') or row['type'] == 'hardlink'
                    else posixpath.join(posixpath.dirname(prefix), target))
            name = posixpath.normpath(posixpath.join(base, *pieces[index:]))
            require(name != '..' and not name.startswith('../'), 'Payload link escapes the root filesystem')
            break
        else:
            return {'resolved_path': name, 'links': links, 'payload': rows.get(name)}
    raise ValueError('Too many dpkg-owned payload links')


def collect_payload(rootfs, manifest, scan):
    os_results = [row for row in scan['Results'] if row.get('Class') == 'os-pkgs']
    require(len(os_results) == 1 and os_results[0]['Type'] == 'ubuntu', 'Exact Ubuntu package evidence required')
    result = os_results[0]
    names = {row['PkgName'] for row in result.get('Vulnerabilities', [])}
    inventory = {row['Name']: row for row in result['Packages']}
    require(len(inventory) == len(result['Packages']), 'Ambiguous complete OS package inventory')
    rows = {row['path']: row for row in manifest}
    require(len(rows) == len(manifest), 'Duplicate actual payload paths')
    with tarfile.open(rootfs) as archive:
        entries = {entry.name.removeprefix('./').rstrip('/'): entry for entry in archive}
        elfs = {}
        for path, entry in entries.items():
            if entry.isfile() and entry.size >= 4:
                with archive.extractfile(entry) as stream:
                    if stream.read(4) == b'\x7fELF':
                        elfs[path] = rows[path]
        status_raw = archive.extractfile(entries['var/lib/dpkg/status']).read()
        status = {}
        for paragraph in status_raw.decode('utf-8').split('\n\n'):
            fields = {}
            for line in paragraph.splitlines():
                for key in ('Package', 'Version', 'Architecture', 'Status', 'Source'):
                    if line.startswith(key + ': '):
                        require(key not in fields, 'Duplicate dpkg status field')
                        fields[key] = line[len(key) + 2:]
            if 'Package' in fields:
                require(fields['Package'] not in status, 'Duplicate installed package status')
                status[fields['Package']] = fields
        packages = {}
        for name in sorted(names):
            require(name in status and name in inventory, 'Finding has no actual installed package')
            fields, package = status[name], inventory[name]
            # Trivy represents Debian epoch, upstream version and revision separately.
            epoch = package.get('Epoch', 0)
            require(type(epoch) is int and epoch >= 0, 'Invalid Debian package epoch')
            version = (str(epoch) + ':' if epoch else '') + package['Version']
            if package.get('Release'):
                version += '-' + package['Release']
            require(fields['Status'] == 'install ok installed' and fields['Version'] == version,
                    'Scanner and actual installed package versions differ')
            candidates = ['var/lib/dpkg/info/' + name + suffix + '.list'
                          for suffix in ('', ':' + fields['Architecture'])]
            selected = [path for path in candidates if path in entries]
            require(len(selected) == 1 and entries[selected[0]].isfile(), 'Ambiguous actual package ownership list')
            body = archive.extractfile(entries[selected[0]]).read()
            declared = body.decode('utf-8').splitlines()
            require(len(declared) == len(set(declared)), 'Duplicate declared package payload')
            owned = {path: resolve_owned_path(path, rows) for path in declared if path != '/.'}
            packages[name] = {'status': fields, 'scanner_package': {key: package.get(key) for key in
                ('Name', 'Version', 'Release', 'Epoch', 'Arch', 'SrcName', 'SrcVersion', 'SrcRelease', 'SrcEpoch')},
                'list_path': selected[0], 'list_sha256': hashlib.sha256(body).hexdigest(),
                'owned_payload': owned}
    inventory_fields = ('Name', 'Version', 'Release', 'Epoch', 'Arch', 'SrcName', 'SrcVersion',
                        'SrcRelease', 'SrcEpoch', 'Licenses')
    return {'packages': packages, 'os_release': scan['Metadata']['OS'],
            'complete_os_package_inventory': {name: {key: package.get(key) for key in inventory_fields}
                                              for name, package in sorted(inventory.items())},
            'complete_elf_payload': elfs,
            'complete_executable_and_link_payload': {path: row for path, row in sorted(rows.items())
                if row['type'] in ('symlink', 'hardlink') or row['type'] == 'file' and row['mode'] & 0o111}}


def subject_hash(subject):
    return hashlib.sha256(json.dumps(subject, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify(facts, scan, rootfs):
    profile_path = ROOT / PROFILE
    require(shared.digest(profile_path) == PROFILE_SHA256, 'Exact Milvus OS review profile changed')
    profile = json.loads(profile_path.read_bytes())
    require(profile['schema'] == 'milvus-os-exact-20261115-v1'
            and profile['risk_acceptance'] is False and profile['binary_redistribution_approved'] is False,
            'Unsupported Milvus OS technical scope')
    subject = profile['subject']
    digest = subject_hash(subject)
    reviews = profile['independent_reviews']
    require({row['actor'] for row in reviews} == {'/root', '/root/milvus_build'} and len(reviews) == 2
            and all(row['subject_sha256'] == digest and row['status'] == 'verified'
                    and row['facts_passed'] is True for row in reviews),
            'Milvus OS scope requires both actual independent reviews')
    for path, expected in subject['source_inputs'].items():
        require(shared.digest(shared.checked(ROOT, path)) == expected, 'Current Milvus scope source changed: ' + path)
    for path, expected in subject['evidence_sha256'].items():
        require(shared.digest(shared.checked(ROOT, path)) == expected, 'Milvus technical evidence changed: ' + path)
    require(shared.runtime_config(facts['inspection']['Config']) == subject['runtime_config'],
            'Actual Milvus runtime configuration changed')
    actual = collect_payload(rootfs, facts['runtime_manifest'], scan)
    require(actual == subject['owned_payload'], 'Complete actual OS package ownership or payload changed')
    os_rows = [finding for row in scan['Results'] if row.get('Class') == 'os-pkgs'
               for finding in row.get('Vulnerabilities', [])]
    expected = {finding_key(row['finding']): row for row in subject['technical_dispositions']}
    require(len(expected) == len(subject['technical_dispositions'])
            and Counter(finding_key(row) for row in os_rows) == Counter(expected.keys()),
            'An exact reviewed Milvus OS finding was added, changed, duplicated or removed')
    rows = {row['path']: row for row in facts['runtime_manifest']}
    require({path: rows.get(path) for path in subject['required_payload']} == subject['required_payload'],
            'Actual Milvus helper, loader or native library payload changed')
    for path in subject['absent_paths']:
        require(path not in rows, 'An explicitly removed vulnerable component returned: ' + path)
    for marker in subject['absent_component_markers']:
        require(not any(marker in path or marker in row.get('target', '') for path, row in rows.items()),
                'An independently excluded vulnerable component returned: ' + marker)
    for row in rows.values():
        require(row.get('sha256') not in subject['removed_elf_sha256'],
                'A removed vulnerable ELF has another payload copy: ' + row['path'])
    return expected
