"""Prepare exact Milvus facts for two independent reviews; never approve them."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_milvus_os_20261115 as gate  # noqa: E402
from scripts import check_grafana_source_build_20261110 as shared  # noqa: E402
from scripts.go_stdlib_rebuild_20261112 import require, verify_trivy_source  # noqa: E402

EVIDENCE = 'deployment/security/milvus-os-20261115-final10-evidence'
REMOVED = {
    'usr/bin/dash': '4f291296e89b784cd35479fca606f228126e3641f5bcaee68dee36583d7c9483',
    'usr/bin/tar': 'fd0d62eed19efd3e115aa1be44160f89d777cd1e6d6d8eb0ce7c8bdc879f59e2',
    'usr/bin/chfn': 'b6c6ba2303b85eb2ade7791bed1b8cef09561b9af9e99bdb972fbe72ad76f210',
    'usr/bin/tic': '1865533aebc1883ae86d4385307e538188ebf7ff2e430f002ebd93fdd594f40e',
    'usr/bin/infocmp': '8193aca52e35dfcd3a69f8e393509015a645c8ecbc340877d05eb951e352fe69',
    'usr/bin/toe': '7e6fbc73f9d183a35128cb0d15fb20909102206d213949ad4cb17d1ad6e18296',
    'usr/lib/x86_64-linux-gnu/libtic.so.6.3': 'c8830fb769e24c870bc0ee9fe82df8af007b5a999df408bcff86e5ba4f2736a7',
}
BASIS = {
    'CVE-2026-102473': ('REPORTED_PROGRAM_REPLACED', 'The original dash ELF and all identical copies are absent; /bin/sh uses the existing Bash in POSIX mode. Original dash also imports libc fnmatch, contrary to this CVE prerequisite.'),
    'CVE-2026-102474': ('VULNERABLE_PROGRAM_REMOVED', 'The original dash printf/echo conv_escape implementation is removed, including its sole ELF payload; /bin/sh is verified Bash POSIX mode.'),
    'CVE-2026-18477': ('VULNERABLE_PROGRAM_REMOVED', 'The actual GNU tar extraction ELF is removed. Official exact Ubuntu source separates extract.c into tar, not rmt.c. Repository backups/restores use independent helper images.'),
    'CVE-2026-18508': ('VULNERABLE_PROGRAM_REMOVED', 'The actual GNU tar extraction ELF and identical copies are absent; separate backup/recovery helper images retain archive operations.'),
    'CVE-2023-29383': ('VULNERABLE_PROGRAM_REMOVED', 'The actual SUID chfn program is removed. Existing passwd/group and runtime user data remain unchanged; no claim that all shadow source was patched.'),
    'CVE-2023-50495': ('VULNERABLE_COMPILER_PAYLOAD_REMOVED', 'Official ncurses compiler implementation is in libtic, not the retained runtime libtinfo reader. The complete ELF graph identifies exactly tic/infocmp/toe consumers; all three, aliases and libtic are removed.'),
    'CVE-2026-107161': ('VULNERABLE_EMBEDDED_COMPONENT_REMOVED', 'The original statically embedded, actually unpatched Cyrus DIGEST-MD5 implementation is replaced by same-ABI librdkafka2.6.1 using the existing system SASL core. All final ELF files lack the embedded plugin implementation and system DIGEST plugins are absent; no Kafka-disabled exemption.'),
    'CVE-2022-27943': ('AFFECTED_COMPONENT_ABSENT', 'The exact libiberty Rust demangler component/tools affected by this source-package advisory are absent from the runtime GCC library payloads and complete ELF inventory.'),
    'CVE-2022-41409': ('AFFECTED_COMPONENT_ABSENT', 'The upstream fix is for the pcre2test test program. This executable and implementation are absent; the runtime PCRE2 package receives the other seven real security fixes.'),
    'CVE-2026-121876': ('AFFECTED_COMPONENT_ABSENT', 'The affected systemd GPT auto-generator executable/implementation is absent; runtime libsystemd/libudev package-source metadata does not install that generator.'),
    'CVE-2026-40228': ('AFFECTED_COMPONENT_ABSENT', 'The affected systemd journal daemon implementation is absent from these runtime library payloads and complete ELF inventory.'),
    'CVE-2022-4899': ('AFFECTED_COMPONENT_ABSENT', 'The upstream fix concerns programs/util.c mallocAndJoin2Dir and the programs/zstdcli.c output-directory input chain. That CLI implementation is absent from the shared compression library and runtime; no claim that arbitrary zstd CLI inputs are safe.'),
    'CVE-2024-56433': ('AFFECTED_COMPONENT_ABSENT', 'The subordinate-ID mapping executables are absent and actual /etc/subuid and /etc/subgid are empty. The advisory entry does not imply that login/passwd ELF code was patched.'),
    'CVE-2026-48962': ('AFFECTED_COMPONENT_ABSENT', 'The exact upstream affected File::GlobMapper module is absent from the complete runtime filesystem; Perl base alone does not provide it.'),
}
for _identifier in ('CVE-2026-67693', 'CVE-2026-88647', 'CVE-2026-88648', 'CVE-2026-95184', 'CVE-2026-95210'):
    BASIS[_identifier] = ('AFFECTED_OPERATION_OUTSIDE_BOUND_ROLE',
        'The GnuTLS library remains affected. This fact is limited to the exact tini -> milvus run standalone role and its fixed curl --noproxy * -f HTTP localhost healthcheck, without redirects/TLS/LDAP/RTMP or runtime APT invocation. Full native consumer graph includes APT http/https launcher aliases, libldap, librtmp, libcurl and curl; no Milvus native chain uses GnuTLS. Reviewed global constructor/init does not perform the vulnerable X509 verification operations. This is not a general library immunity or package-version fix.')


def prepare(args):
    facts = json.loads((args.facts_dir / 'facts.json').read_bytes())
    scan = json.loads(args.scan.read_bytes())
    runtime = shared.runtime_config(facts['inspection']['Config'])
    verify_trivy_source(scan, facts['inspection'], {**facts, 'runtime_config': runtime})
    owned = gate.collect_payload(args.facts_dir / 'rootfs.tar', facts['runtime_manifest'], scan)
    rows = {row['path']: row for row in facts['runtime_manifest']}
    absent = sorted([*REMOVED, 'usr/bin/captoinfo', 'usr/bin/infotocap', 'usr/lib/x86_64-linux-gnu/libtic.so.6'])
    require(not any(path in rows for path in absent), 'A removed vulnerable program is present')
    old_kafka = 'a6ce7e88ec18acb9a66a365ef184c763e61532cb1483d76f97221516d8af168a'
    removed_hashes = sorted([*REMOVED.values(), old_kafka])
    require(not any(row.get('sha256') in removed_hashes for row in rows.values()), 'A vulnerable ELF copy remains')
    require(rows['usr/bin/sh']['target'] == 'bash', 'POSIX shell replacement differs')
    evidence = ROOT / EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)
    hashes = {}

    def copy(source, relative):
        destination = evidence / (relative + '.gz')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
        hashes[destination.relative_to(ROOT).as_posix()] = shared.digest(destination)

    preliminary = json.loads((args.peer_dir / 'recommendations.json').read_bytes())
    copy(args.peer_dir / 'recommendations.json', 'peer-initial/recommendations.json')
    official_pages = json.loads((args.peer_dir / 'official-source-receipts.json').read_bytes())
    filelist_sources = json.loads((args.peer_dir / 'upstream-source-receipts.json').read_bytes())
    private_webpages = {page['id'] + extension for page in official_pages['sources']
                        for extension in ('.html', '.txt')}
    filelists = [row for row in filelist_sources if row.get('file') in
                 ('cyrus-core-filelist.html', 'cyrus-modules-filelist.html')]
    require(len(official_pages['sources']) == 19 and len(filelists) == 2,
            'Official webpage reference inventory changed and requires new review')
    private_webpages.update(row['file'] for row in filelists)
    references = []
    for row in official_pages['sources']:
        page = args.peer_dir / (row['id'] + '.html')
        require(shared.digest(page) == row['sha256'] and page.stat().st_size == row['bytes'],
                'Official CVE receipt does not match the retained private response')
        derived = args.peer_dir / (row['id'] + '.txt')
        references.append({**row, 'observed_at_utc': official_pages['observed_at_utc'],
                           'local_derived_plaintext': {'sha256': shared.digest(derived),
                                                       'bytes': derived.stat().st_size}})
    for row in filelists:
        page = args.peer_dir / row['file']
        require(shared.digest(page) == row['sha256'] and page.stat().st_size == row['bytes'],
                'Official package filelist receipt does not match the retained private response')
        references.append({**row, 'observed_at_utc': None,
                           'time_unknown_reason': 'Original transport receipt did not record retrieval time.'})
    webpage_references = {'schema': 'official-webpage-factual-reference-v1',
        'boundary': 'Factual URLs, transport observations and byte identities only. Rendered website HTML and locally derived full-page plaintext remain private; public readability is not redistribution permission.',
        'sources': references}
    for name, expected in preliminary['evidence_sha256'].items():
        path = args.peer_dir / name
        require(shared.digest(path) == expected, 'Original independent source evidence changed: ' + name)
        if not name.endswith('.elf') and name not in private_webpages:
            copy(path, 'peer-initial/' + name)
    reference_path = evidence / 'peer-initial/official-webpage-reference-receipts.json.gz'
    reference_path.write_bytes(gzip.compress((json.dumps(webpage_references, indent=2) + '\n').encode(), mtime=0))
    hashes[reference_path.relative_to(ROOT).as_posix()] = shared.digest(reference_path)
    licence_root = ROOT / 'deployment/security/milvus-os-20261115-source-licenses'
    licence_map = json.loads((licence_root / 'source-license-map.json').read_bytes())
    require(len(licence_map['source_or_patch_entries']) == 48
            and len(licence_map['license_sidecars']) == 15, 'Source evidence licence inventory changed')
    for item in licence_map['source_or_patch_entries']:
        require(shared.digest(ROOT / item['path']) == item['sha256'],
                'Covered original source or patch changed: ' + item['path'])
    for item in licence_map['license_sidecars']:
        path = licence_root / 'licenses' / item['file']
        require(shared.digest(path) == item['sha256'] and path.stat().st_size == item['bytes'],
                'Original full licence or attribution changed: ' + item['file'])
        hashes[path.relative_to(ROOT).as_posix()] = shared.digest(path)
    for name in ('NOTICE.md', 'source-license-map.json', '.gitattributes'):
        path = licence_root / name
        hashes[path.relative_to(ROOT).as_posix()] = shared.digest(path)
    for name in ('all-elf.json', 'original-all-elf.json', 'c-api-independent.json',
                 'c-api-old.stdout', 'c-api-new.stdout', 'loader-independent.json',
                 'gnutls-direct-consumers.json', 'gnutls-transitive-consumers.json',
                 'official-tag-independent.json', 'official-commit-independent.json', 'elf-capture.go'):
        copy(args.independent_dir / name, 'peer-final/' + name)
    for name in ('independent-native-and-payload-review.json', 'all-elf-difference.json',
                 'all-elf-modification-owners.json', 'raw-scan-transition.json'):
        path = args.independent_dir / name
        if path.exists():
            copy(path, 'peer-final/' + name)
    log_base = ROOT / '.cache/release-v015-20261115/runtime'
    for name in ('milvus-build-07.log', 'milvus-build-08.log', 'milvus-build-09.log', 'milvus-build-10.log',
                 'milvus-final-10-permission-delta.json', 'milvus-source-permissions-probe-10.log',
                 'milvus-posix-probe-01.log',
                 'milvus-health-probe-01.log', 'milvus-health-probe-02.log',
                 'milvus-health-probe-02-receipt.json', 'milvus-repository-tool-uses-01.log',
                 'milvus-terminfo-tool-uses-01.log', 'milvus-all-go-process-sites-01.json',
                 'milvus-go-exec-import-sites-01.json', 'milvus-final-source-inputs-01.json',
                 'milvus-final-source-inputs-02.json', 'milvus-final-source-inputs-03.json'):
        copy(log_base / name, 'implementation/' + name)
    copy(log_base / 'independent-milvus-final-03/independent-final10-delta.json', 'peer-final10/independent-delta.json')
    for name in ('api-result.json', 'actual-isolated-resources.json',
                 'health-history.json'):
        copy(log_base / 'milvus-standalone-01' / name, 'actual-regression/' + name)
    copy(args.scan, 'final-complete-raw.json')
    copy(ROOT / 'scripts/prepare_milvus_os_profile_20261115.py', 'prepare-source.py')
    wanted = {row['path'] for row in facts['runtime_manifest'] if row['type'] in ('file', 'symlink', 'hardlink')
              and (row['path'].startswith(('milvus/lib/', 'usr/share/deepresearch/milvus-rdkafka/'))
                   or row['path'] in ('tini', 'usr/bin/bash', 'usr/bin/sh', 'usr/bin/curl',
                   'etc/subuid', 'etc/subgid', 'etc/passwd', 'etc/group', 'etc/login.defs', 'milvus/configs/milvus.yaml')
                   or row['path'].startswith('usr/lib/apt/methods/')
                   or row['path'] in ('usr/bin/apt', 'usr/bin/apt-get', 'usr/bin/apt-cache'))}
    wanted.update(('usr/share/deepresearch', 'usr/share/deepresearch/milvus-rdkafka'))
    sources = [ROOT / 'Dockerfile.milvus', ROOT / 'deployment/vendors/milvus/source.lock.json',
               ROOT / 'compose.yaml', ROOT / 'compose.production.yaml', ROOT / 'deployment/compose/compose.verify.yaml',
               *sorted((ROOT / 'deployment/vendors/milvus/rdkafka-20261115').iterdir())]
    dispositions = []
    for result in scan['Results']:
        if result.get('Class') != 'os-pkgs':
            continue
        for row in result.get('Vulnerabilities', []):
            require(row['VulnerabilityID'] in BASIS, 'A newly observed OS finding requires new review')
            category, basis = BASIS[row['VulnerabilityID']]
            dispositions.append({'finding': {key: row[key] for key in ('VulnerabilityID', 'PkgName',
                'InstalledVersion', 'Severity', 'Status', 'FixedVersion') if key in row},
                'disposition': category, 'technical_basis': basis, 'risk_acceptance': False})
    require(len(dispositions) == 31, 'Current complete OS finding inventory changed')
    permission_delta = json.loads((log_base / 'milvus-final-10-permission-delta.json').read_bytes())
    require(permission_delta['new_image'] == facts['image_id']
            and permission_delta['new_runtime_manifest_sha256'] == facts['runtime_manifest_sha256']
            and permission_delta['all_other_payload_rows_exact'] is True
            and permission_delta['runtime_config_exact'] is True and len(permission_delta['delta']) == 7,
            'Scoped real API regression reuse does not match the exact final permission-only delta')
    subject = {'boundary': 'Exact current standalone role only. Original full raw and affected library versions are retained; no general library immunity, severity filtering or binary redistribution approval.',
        'regression_reuse': permission_delta,
        'source_inputs': {path.relative_to(ROOT).as_posix(): shared.digest(path) for path in sources if path.is_file()},
        'runtime_config': runtime, 'owned_payload': owned,
        'required_payload': {path: rows[path] for path in sorted(wanted)}, 'absent_paths': absent,
        'removed_elf_sha256': removed_hashes,
        'absent_component_markers': ['libdigestmd5', 'File/GlobMapper.pm', 'systemd-gpt-auto-generator',
                                     'systemd-journald', '/pcre2test', '/newuidmap', '/newgidmap',
                                     '/zstd', '/unzstd', '/zstdcat', 'rust-demangle', 'libiberty'],
        'technical_dispositions': dispositions, 'evidence_sha256': hashes}
    profile = {'schema': 'milvus-os-exact-20261115-v1', 'risk_acceptance': False,
               'binary_redistribution_approved': False, 'subject': subject, 'independent_reviews': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(profile, indent=2) + '\n').encode())
    print(json.dumps({'status': 'prepared_not_approved', 'subject_sha256': gate.subject_hash(subject),
                      'packages': len(owned['packages']), 'findings': len(dispositions), 'evidence': len(hashes)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facts-dir', type=Path, required=True)
    parser.add_argument('--scan', type=Path, required=True)
    parser.add_argument('--peer-dir', type=Path, required=True)
    parser.add_argument('--independent-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args())
