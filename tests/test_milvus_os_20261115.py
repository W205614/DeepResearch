from copy import deepcopy
import hashlib
import io
import json
import tarfile

import pytest

from scripts import check_milvus_os_20261115 as gate


def write_image(path, files, links):
    manifest = []
    with tarfile.open(path, 'w') as archive:
        for name, (body, mode) in files.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(body), mode
            archive.addfile(entry, io.BytesIO(body))
            manifest.append({'path': name, 'type': 'file', 'mode': mode, 'uid': 0, 'gid': 0,
                             'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
        for name, (target, mode) in links.items():
            entry = tarfile.TarInfo(name)
            entry.type, entry.linkname, entry.mode = tarfile.SYMTYPE, target, mode
            archive.addfile(entry)
            manifest.append({'path': name, 'type': 'symlink', 'mode': mode,
                             'uid': 0, 'gid': 0, 'target': target})
    return manifest


@pytest.fixture
def reviewed_unit_image(tmp_path, monkeypatch):
    finding = {'VulnerabilityID': 'UNIT-ONLY', 'PkgName': 'fixture',
               'InstalledVersion': '1:1-2', 'Severity': 'LOW'}
    scan = {'Metadata': {'OS': {'Family': 'ubuntu', 'Name': '22.04'}}, 'Results': [{
        'Class': 'os-pkgs', 'Type': 'ubuntu', 'Vulnerabilities': [finding],
        'Packages': [{'Name': 'fixture', 'Version': '1', 'Release': '2', 'Epoch': 1, 'Arch': 'amd64'}]}]}
    files = {
        'var/lib/dpkg/status': (b'Package: fixture\nVersion: 1:1-2\nArchitecture: amd64\nStatus: install ok installed\n\n', 0o644),
        'var/lib/dpkg/info/fixture.list': (b'/.\n/bin/tool\n', 0o644),
        'usr/bin/tool': (b'\x7fELFunit-only', 0o755),
        'etc/subuid': (b'', 0o644),
    }
    links = {'bin': ('usr/bin', 0o777)}
    rootfs = tmp_path / 'rootfs.tar'
    manifest = write_image(rootfs, files, links)
    config = gate.shared.runtime_config({})
    subject = {'source_inputs': {}, 'evidence_sha256': {}, 'runtime_config': config,
               'owned_payload': gate.collect_payload(rootfs, manifest, scan),
               'required_payload': {'etc/subuid': next(row for row in manifest if row['path'] == 'etc/subuid')},
               'absent_paths': [], 'removed_elf_sha256': [],
               'absent_component_markers': ['File/GlobMapper.pm'],
               'technical_dispositions': [{'finding': finding, 'disposition': 'UNIT_ONLY'}]}
    # This synthetic profile exists only in pytest's temporary directory.
    profile = {'schema': 'milvus-os-exact-20261115-v1', 'risk_acceptance': False,
               'binary_redistribution_approved': False, 'subject': subject,
               'independent_reviews': [{'actor': actor, 'subject_sha256': gate.subject_hash(subject),
                    'status': 'verified', 'facts_passed': True} for actor in ('/root', '/root/milvus_build')]}
    raw = (json.dumps(profile) + '\n').encode()
    (tmp_path / 'profile.json').write_bytes(raw)
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    monkeypatch.setattr(gate, 'PROFILE', 'profile.json')
    monkeypatch.setattr(gate, 'PROFILE_SHA256', hashlib.sha256(raw).hexdigest())
    facts = {'inspection': {'Config': {}}, 'runtime_manifest': manifest}
    return rootfs, files, links, scan, facts, subject


def test_complete_owned_files_resolve_usr_merge_and_debian_revision(reviewed_unit_image):
    rootfs, _, _, scan, facts, subject = reviewed_unit_image
    row = subject['owned_payload']['packages']['fixture']
    assert row['status']['Version'] == '1:1-2'
    assert row['owned_payload']['/bin/tool']['resolved_path'] == 'usr/bin/tool'
    assert row['owned_payload']['/bin/tool']['payload']['type'] == 'file'
    assert gate.verify(facts, scan, rootfs)


@pytest.mark.parametrize('change', ['new-package', 'renamed-unowned-elf', 'shadow-curl-script',
                                   'zero-mode-shadow-alias', 'returned-perl-module', 'changed-owned-elf',
                                   'subordinate-id-range'])
def test_unreviewed_package_consumer_or_entry_shadow_blocks(reviewed_unit_image, change):
    rootfs, files, links, scan, facts, _ = reviewed_unit_image
    scan = deepcopy(scan)
    if change == 'new-package':
        scan['Results'][0]['Packages'].append({'Name': 'new-consumer', 'Version': '1'})
    elif change == 'renamed-unowned-elf':
        files['usr/lib/sasl2/libother.so'] = (b'\x7fELFnew-consumer', 0o644)
    elif change == 'shadow-curl-script':
        files['usr/local/bin/curl'] = (b'#!/bin/sh\nexec /usr/bin/curl https://example.invalid\n', 0o755)
    elif change == 'zero-mode-shadow-alias':
        links['usr/local/bin/curl'] = ('/usr/bin/tool', 0)
    elif change == 'returned-perl-module':
        files['usr/share/perl5/File/GlobMapper.pm'] = (b'package File::GlobMapper; 1;\n', 0o644)
    elif change == 'subordinate-id-range':
        files['etc/subuid'] = (b'fixture:100000:65536\n', 0o644)
    else:
        files['usr/bin/tool'] = (b'\x7fELFchanged-body', 0o755)
    facts['runtime_manifest'] = write_image(rootfs, files, links)
    with pytest.raises(ValueError, match='payload changed|component returned'):
        gate.verify(facts, scan, rootfs)
