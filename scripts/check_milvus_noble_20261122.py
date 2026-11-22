"""Verify complete Noble package evidence and exact standalone Milvus dispositions."""
from __future__ import annotations

from collections import Counter
import hashlib
import tarfile

from scripts import check_milvus_os_20261115 as legacy
from scripts.go_stdlib_rebuild_20261112 import require


LEGACY_RECIPE_SHA256 = '1d84d19b692bd043584c6792773eb2fd44f0c3effc682046b2cda24a1e3d2ee5'
NOBLE_RECIPE = 'deployment/docker/Dockerfile.milvus-noble-20261122'
NOBLE_RECIPE_SHA256 = '9e7cab85626c503257db9490991b2284088a1b7d0c4957e8e8831eb0bdcdb8d4'
CURL_SHA256 = 'c62ec1b00124a398682a1c246f0406ac520fc79c01e26a1b78bd3f17510f69ea'
SIGNER = b'VALIDSIG C4493CB739F4A89F9852CBC20CBA08575F8372DF '
EMPTY_LAYER = 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef'
OS_INVENTORY_SHA256 = '66b1421cb650848e3a7cf14d320a1b86fdd198152f3a1c39c9de4b6b7d22c7e8'
OS_FINDINGS = {
    ('CVE-2026-102473', 'dash', '0.5.12-6ubuntu5', 'MEDIUM', 'affected', ''): 'AFFECTED_RUNTIME_EXECUTABLE_REMOVED',
    ('CVE-2026-102474', 'dash', '0.5.12-6ubuntu5', 'MEDIUM', 'affected', ''): 'AFFECTED_RUNTIME_EXECUTABLE_REMOVED',
    ('CVE-2026-18477', 'tar', '1.35+dfsg-3ubuntu0.4', 'MEDIUM', 'affected', ''): 'AFFECTED_RUNTIME_EXECUTABLE_REMOVED',
    ('CVE-2026-18508', 'tar', '1.35+dfsg-3ubuntu0.4', 'MEDIUM', 'affected', ''): 'AFFECTED_RUNTIME_EXECUTABLE_REMOVED',
    **{('CVE-2026-107161', package, '2.1.28+dfsg1-5ubuntu3.1', 'MEDIUM', 'affected', ''):
       'AFFECTED_SASL_MECHANISM_NOT_INSTALLED' for package in ('libsasl2-2', 'libsasl2-modules-db')},
}


def verify(facts, scan, rootfs):
    require(hashlib.sha256((legacy.ROOT / NOBLE_RECIPE).read_bytes()).hexdigest() == NOBLE_RECIPE_SHA256
            and hashlib.sha256((legacy.ROOT / 'Dockerfile.milvus').read_bytes()).hexdigest() == LEGACY_RECIPE_SHA256,
            'The reviewed Noble runtime recipe or retained Milvus application build changed')
    payload = legacy.collect_payload(rootfs, facts['runtime_manifest'], scan)
    rows = {row['path']: row for row in facts['runtime_manifest']}
    config = facts['inspection']['Config']
    require(payload['os_release'] == {'Family': 'ubuntu', 'Name': '24.04'}
            and facts['diff_ids'][1:] in ([], [EMPTY_LAYER])
            and facts['diff_ids'][0] != EMPTY_LAYER
            and config['User'] == 'milvus:milvus' and config['Entrypoint'] == ['/tini', '--']
            and 'LD_LIBRARY_PATH=/opt/milvus-krb5/lib:/milvus/lib:/usr/lib' in config['Env'],
            'Milvus Noble platform, layers or runtime configuration changed')
    inventory = payload['complete_os_package_inventory']
    require(len(inventory) == 78 and legacy.subject_hash(inventory) == OS_INVENTORY_SHA256
            and all(inventory[name]['Version'] + '-' + inventory[name]['Release'] == '2.39-0ubuntu8.12'
                    for name in ('libc6', 'libc-bin'))
            and not {'apt', 'gpgv', 'libgnutls30t64', 'libsystemd0', 'libudev1'} & inventory.keys(),
            'Complete Milvus Noble package inventory or signed libc update differs')
    os_result, = [result for result in scan['Results'] if result.get('Class') == 'os-pkgs']
    require(Counter(legacy.finding_key(row) for row in os_result.get('Vulnerabilities', []))
            == Counter(OS_FINDINGS.keys()), 'The exact Milvus Noble advisory tuples changed')
    for name in ('dash', 'tar'):
        owned = payload['packages'][name]['owned_payload']
        live_elfs = {(path, item['resolved_path']) for path, item in owned.items()
                     if item['resolved_path'] in payload['complete_elf_payload']}
        shell_replacement = {('/usr/bin/sh', 'usr/bin/bash')} if name == 'dash' else set()
        require(live_elfs == shell_replacement
                and not any(path.rsplit('/', 1)[-1] == name for path in payload['complete_executable_and_link_payload']),
                'An affected Milvus runtime tool returned: ' + name)
    require(rows['usr/bin/sh']['target'] == 'bash'
            and rows['usr/bin/curl']['sha256'] == CURL_SHA256
            and not any('digestmd5' in path.lower() or 'digestmd5' in row.get('target', '').lower()
                        for path, row in rows.items()),
            'Milvus shell, fixed curl or Cyrus SASL mechanism boundary changed')
    for library in ('libgssapi_krb5.so.2', 'libkrb5.so.3', 'libk5crypto.so.3',
                    'libkrb5support.so.0', 'libcom_err.so.3'):
        owned = legacy.resolve_owned_path('/opt/milvus-krb5/lib/' + library, rows)
        require(owned['resolved_path'] in payload['complete_elf_payload'],
                'The fixed Milvus Kerberos library is absent: ' + library)
    with tarfile.open(rootfs) as archive:
        signature = archive.extractfile('opt/milvus-krb5/lib/kerberos-signature.txt').read()
        require(SIGNER in signature and b'BADSIG' not in signature, 'Milvus Kerberos release signature is missing')
    return {key: {'disposition': reason} for key, reason in OS_FINDINGS.items()}
