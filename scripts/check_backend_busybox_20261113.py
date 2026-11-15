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
TRANSPORT_PROFILE = Path('deployment/security/backend-busybox-20261115-transport-profile.json')
RETAINED_PROFILE_SHA256 = 'c32273c12b1b932a1aab4197741cbeb424d4a7c86475674bc44e4a631a5c0d8a'
TRANSPORT_SCHEMA = 'backend-busybox-exact-transport-continuation-20261115-v1'
RECIPE = 'business-backend/Dockerfile'
CHECKER = 'scripts/check_backend_busybox_20261113.py'
PRODUCER = 'scripts/prepare_backend_busybox_transport_20261115.py'
LEGAL_RECIPE = 'corresponding-source/backend-integration/Dockerfile'
ASSET_PREFIX = 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/'
TRANSPORT_ASSETS = [
    ('665302157', 'OpenJDK21U-jre_x64_alpine-linux_hotspot_21.0.13_9.tar.gz', 52331592,
     '9138eab0f7d230e17163953f015ec9ae2431dbc49069c658b7f991e45b54fb9b'),
    ('665302362', 'OpenJDK21U-jre_x64_alpine-linux_hotspot_21.0.13_9.tar.gz.sig', 310,
     '585cec46495c43103a397e07829a1a8026d0fdc6c63647ba44ac9c5d6b1f5d9c'),
    ('665302375', 'OpenJDK21U-sbom_x64_alpine-linux_hotspot_21.0.13_9.json', 172488,
     'daf3c6b803ecc5286d3c4eadc913ab8ebdd72c22f575aed364704a4cdc11bcb4'),
    ('665299889', 'OpenJDK21U-sbom_x64_alpine-linux_hotspot_21.0.13_9.json.sig', 310,
     '719a53f61fe6e3565388b34cf3cdb31fa0c9fef9446379cf080e7b5bda96a998'),
]
LEGAL_ROOT = '/usr/share/deepresearch/backend-busybox'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def load_profile(root=ROOT):
    if (root / TRANSPORT_PROFILE).is_file():
        return load_transport_profile(root)
    profile = json.loads((root / PROFILE).read_bytes())
    require(profile['schema'] == 'backend-busybox-fixed-source-20261113-v1', 'Backend BusyBox profile schema differs')
    require(profile['risk_acceptance'] is False and profile['raw_report_modified'] is False,
            'Backend BusyBox review cannot accept risk or alter raw findings')
    for name, expected in {**profile['source_inputs'], **profile['evidence_sha256']}.items():
        path = root / name
        require(path.is_file() and path.resolve().is_relative_to(root.resolve()) and sha(path.read_bytes()) == expected,
                'Backend BusyBox reviewed source changed: ' + name)
    return profile


def transport_assets():
    return [{'original_url': 'https://api.github.com/repos/adoptium/temurin21-binaries/releases/assets/' + asset,
             'url': ASSET_PREFIX + name, 'bytes': size, 'sha256': digest}
            for asset, name, size, digest in TRANSPORT_ASSETS]


def load_transport_profile(root=ROOT):
    retained_raw = (root / PROFILE).read_bytes()
    require(sha(retained_raw) == RETAINED_PROFILE_SHA256, 'Original Backend BusyBox profile changed')
    retained = json.loads(retained_raw)
    profile = json.loads((root / TRANSPORT_PROFILE).read_bytes())
    require(profile['schema'] == TRANSPORT_SCHEMA, 'Backend transport profile schema differs')
    continuation = profile['transport_continuation']
    before = continuation['original_recipe_utf8'].encode('utf-8')
    require(continuation['retained_profile_sha256'] == RETAINED_PROFILE_SHA256
            and len(before) == 4819 and sha(before) == retained['source_inputs'][RECIPE]
            and continuation['assets'] == transport_assets(), 'Backend transport original source or assets differ')
    after = before
    for asset in continuation['assets']:
        original, current = asset['original_url'].encode(), asset['url'].encode()
        require(after.count(original) == 1, 'Backend transport original URL is not unique')
        after = after.replace(original, current)
    require((root / RECIPE).read_bytes() == after, 'Backend recipe differs beyond the four exact transport URLs')
    expected_inputs = {**retained['source_inputs'], RECIPE: sha(after),
                       CHECKER: sha((root / CHECKER).read_bytes()), PRODUCER: sha((root / PRODUCER).read_bytes())}
    require(profile['source_inputs'] == expected_inputs, 'Backend transport source input set differs')
    require(profile['evidence_sha256'] == {**retained['evidence_sha256'], PROFILE.as_posix(): RETAINED_PROFILE_SHA256},
            'Backend transport retained evidence differs')
    for name, expected in {**profile['source_inputs'], **profile['evidence_sha256']}.items():
        path = root / name
        require(path.is_file() and path.resolve().is_relative_to(root.resolve()) and sha(path.read_bytes()) == expected,
                'Backend BusyBox reviewed source changed: ' + name)
    rebuilt = {**retained['legal_source_files'], LEGAL_RECIPE: {'bytes': len(after), 'sha256': sha(after)}}
    require(profile['legal_source_variants'] == {'retained': retained['legal_source_files'], 'rebuilt': rebuilt},
            'Backend complete legal/source variants differ')
    changed = {'schema', 'scope', 'source_inputs', 'evidence_sha256', 'transport_continuation',
               'legal_source_variants', 'collection'}
    require(set(profile) == set(retained) | {'transport_continuation', 'legal_source_variants', 'collection'}
            and all(profile[key] == retained[key] for key in retained if key not in changed),
            'Backend fixed payload, ABI, raw findings or original contract changed')
    require(profile['scope'] == 'Exact four-URL transport continuation; both complete retained and rebuilt source closures.',
            'Backend transport review scope differs')
    collection = profile['collection']
    require(collection['schema'] == 'actual-backend-busybox-target-export-v1'
            and collection['target'] == 'backend-busybox-fix'
            and collection['os'] == 'linux' and collection['architecture'] == 'amd64'
            and collection['actual_busybox'] == retained['payload']['/bin/busybox']
            and collection['legal_regular_files'] == len(rebuilt)
            and collection['changed_legal_files'] == [LEGAL_RECIPE]
            and collection['new_java_compilation_or_jre_scan_claimed'] is False
            and collection['independent_review_claimed'] is False,
            'Backend transport target collection contract differs')
    return profile


def legal_source_variant(closure, profile):
    variants = (profile['legal_source_variants'] if profile['schema'] == TRANSPORT_SCHEMA
                else {'retained': profile['legal_source_files']})
    matches = [name for name, expected in variants.items() if closure == expected]
    require(len(matches) == 1, 'Backend BusyBox complete legal/source closure differs')
    return matches[0]


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
    source_variant = legal_source_variant(closure, profile)
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
            'profile_sha256': sha((root / (TRANSPORT_PROFILE if profile['schema'] == TRANSPORT_SCHEMA else PROFILE)).read_bytes()),
            'legal_source_variant': source_variant,
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
