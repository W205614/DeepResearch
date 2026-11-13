"""Adjudicate only the exact fixed Backend BusyBox payload and three retained CPE rows."""
from collections import Counter
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import struct
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path('deployment/security/backend-busybox-20261113-profile.json')
LEGAL_ROOT = '/usr/share/deepresearch/backend-busybox'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def load_profile(root=ROOT):
    profile = json.loads((root / PROFILE).read_bytes())
    require(profile['schema'] == 'backend-busybox-fixed-source-20261113-v1', 'Backend BusyBox profile schema differs')
    require(profile['risk_acceptance'] is False and profile['raw_report_modified'] is False,
            'Backend BusyBox review cannot accept risk or alter raw findings')
    for name, expected in {**profile['source_inputs'], **profile['evidence_sha256']}.items():
        path = root / name
        require(path.is_file() and path.resolve().is_relative_to(root.resolve()) and sha(path.read_bytes()) == expected,
                'Backend BusyBox reviewed source changed: ' + name)
    return profile


def archive_files(raw):
    require(len(raw) <= 32 * 1024 * 1024, 'Backend legal/source archive exceeds its bound')
    result = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            name = PurePosixPath(member.name).as_posix().removeprefix('./')
            require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,
                    'Unsafe Backend legal/source member')
            if member.isdir():
                continue
            require(member.isfile() and name not in result and len(result) < 250,
                    'Backend legal/source closure has an unexpected member')
            result[name] = {'bytes': member.size, 'sha256': sha(archive.extractfile(member).read())}
    return result


def link_target(raw):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        require(len(members) == 1 and members[0].issym(), 'Backend /bin/sh is not the reviewed symlink')
        return members[0].linkname


def assess(report, sbom, busybox, ssl_client, shell_tar, legal_tar, musl, *, root=ROOT):
    profile = load_profile(root)
    expected = profile['payload']
    for path, raw in [('/bin/busybox', busybox), ('/usr/bin/ssl_client', ssl_client),
                      ('/lib/ld-musl-x86_64.so.1', musl)]:
        require(raw[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', raw, 18)[0] == 62,
                'Backend BusyBox component is not ELF64 little-endian x86-64')
        require({'bytes': len(raw), 'sha256': sha(raw)} == expected[path], 'Actual Backend BusyBox payload differs: ' + path)
        files = [row for row in sbom['files'] if row['location']['path'] == path]
        require(len(files) == 1 and any(d['algorithm'] == 'sha256' and d['value'] == sha(raw) for d in files[0]['digests']),
                'Backend BusyBox SBOM differs from the actual file bytes')
        if path in profile['needed_libraries']:
            require(files[0]['executable']['importedLibraries'] == profile['needed_libraries'][path],
                    'Backend BusyBox native dependency boundary changed')
    require(link_target(shell_tar) == '/bin/busybox', 'Backend shell no longer points to the shared fixed payload')
    closure = archive_files(legal_tar)
    require(closure == profile['legal_source_files'], 'Backend BusyBox complete legal/source closure differs')
    for name in ('metadata/build.json', 'metadata/backend-benign-regression.json'):
        require(name in closure, 'Backend BusyBox build or ordinary regression receipt is missing')
    wanted = Counter(tuple(row) for row in profile['retained_finding_identities'])
    observed = Counter()
    for row in report['matches']:
        finding, artifact = row['vulnerability'], row['artifact']
        observed[(finding['id'], artifact['name'], artifact['version'], artifact['type'],
                  finding['severity'], finding['namespace'])] += 1
    require(observed == wanted and not report.get('ignoredMatches'),
            'Backend full all-severity findings differ from the exact three-row review')
    require(report['source']['target']['imageID'] == sbom['source']['metadata']['imageID'],
            'Backend BusyBox scan and actual inventory identities differ')
    return {'passed': True, 'all_severity_technical_clear': True, 'raw_findings': sum(observed.values()),
            'raw_report_modified': False, 'risk_acceptance': False,
            'profile_sha256': sha((root / PROFILE).read_bytes()),
            'dispositions': profile['dispositions'], 'legal_source_regular_files': len(closure),
            'actual_fixed_busybox_sha256': sha(busybox), 'actual_standalone_ssl_client_sha256': sha(ssl_client),
            'boundary': 'Only these exact component bytes, source closure and retained finding identities; no raw-zero claim.'}


def collect_and_assess(container, report, sbom, output, run):
    output.mkdir(exist_ok=False)
    payloads = {}
    for name, path in [('busybox', '/bin/busybox'), ('ssl_client', '/usr/bin/ssl_client'),
                       ('musl', '/lib/ld-musl-x86_64.so.1')]:
        target = output / name
        run(['docker', 'cp', container + ':' + path, str(target)])
        payloads[name] = target.read_bytes()
    shell = run(['docker', 'cp', container + ':/bin/sh', '-'])
    legal = run(['docker', 'cp', container + ':' + LEGAL_ROOT + '/.', '-'])
    (output / 'shell-link.tar').write_bytes(shell)
    (output / 'legal-source.tar').write_bytes(legal)
    verdict = assess(report, sbom, payloads['busybox'], payloads['ssl_client'], shell, legal, payloads['musl'])
    (output / 'verdict.json').write_text(json.dumps(verdict, indent=2) + '\n', encoding='utf-8')
    return verdict
