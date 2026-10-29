"""Apply two new-main technical facts after preserving the old source2 verdict.

This is a manifest applicability gate, never an OCI deployment approval. It keeps
all raw rows and the old exact-two result, and cannot retire the old jose alert.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_fs_source_applicability as old  # noqa: E402
from scripts.security_review_policy import validate_review_policy  # noqa: E402

REVIEW_PATH = 'deployment/security/grafana-candidate-source2-technical-review.json'
REVIEW_SHA256 = '5603568f371240b2b2f31dcd322e95b8fd4e1e22e051d15f8e89d6f3b43ff6ad'
EVIDENCE = 'deployment/security/grafana-candidate-review-evidence/'
SOURCE = 'deployment/vendors/grafana-security/main/go.mod'
MODULE = 'github.com/grafana/tempo'
VERSION = 'v1.5.1-0.20260427112133-525d1bab07e0'
TARGET = 'usr/share/grafana/bin/grafana'
IDS = frozenset({'CVE-2026-21728', 'CVE-2026-28377'})
DISPOSITION = 'NOT_APPLICABLE_TO_EXACT_BOUND_MAIN_BUILD'
require = old.require


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(root, relative):
    return old.checked_file(root, relative)


def native_modules(text):
    modules, current = {}, None
    settings, path = {}, None
    for line in text.splitlines()[1:]:
        fields = line.strip().split('\t')
        if fields[0] in ('mod', 'dep'):
            require(len(fields) in (3, 4) and fields[1] not in modules,
                    'Native module identity is malformed or repeated')
            current = fields[1]
            modules[current] = dict(Path=current, Version=fields[2], Sum=fields[3] if len(fields) == 4 else '')
        elif fields[0] == '=>':
            require(current is not None and len(fields) in (3, 4) and 'Replace' not in modules[current],
                    'Native replacement is malformed or repeated')
            modules[current]['Replace'] = dict(Path=fields[1], Version=fields[2],
                                               Sum=fields[3] if len(fields) == 4 else '')
        elif fields[0] == 'path':
            require(path is None and len(fields) == 2, 'Native command path is ambiguous')
            path = fields[1]
        elif fields[0] == 'build':
            require(len(fields) == 2 and '=' in fields[1], 'Native build setting is malformed')
            key, value = fields[1].split('=', 1)
            require(key not in settings, 'Native build setting is repeated')
            settings[key] = value
    require(modules and path == 'github.com/grafana/grafana/pkg/cmd/grafana', 'Different native command')
    return modules, settings


def effective(module):
    module = module.get('Replace', module)
    return module['Path'], module.get('Version') or '', module.get('Sum') or ''


def graph_facts(raw, native_text):
    """Reparse every package and compare every effective native dependency."""
    decoder = json.JSONDecoder(object_pairs_hook=old.unique_object, parse_constant=old.invalid_constant)
    text, cursor, packages, modules, imports = raw.decode('utf-8'), 0, {}, {}, set()
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text):
            break
        package, cursor = decoder.raw_decode(text, cursor)
        require(type(package) is dict and 'Error' not in package
                and ('Incomplete' not in package or package['Incomplete'] is False)
                and ('DepsErrors' not in package or type(package['DepsErrors']) is list and not package['DepsErrors']),
                'Production graph is incomplete')
        name = package.get('ImportPath')
        require(type(name) is str and name and name not in packages, 'Production package is missing or duplicated')
        require(name != MODULE and not name.startswith(MODULE + '/'), 'Tempo package is compiled into main')
        values = package.get('Imports', [])
        require(type(values) is list and all(type(v) is str and v for v in values), 'Production imports are malformed')
        imports.update(values)
        packages[name] = package
        if 'Module' not in package:
            continue
        module = package['Module']
        require(type(module) is dict and type(module.get('Path')) is str and module['Path'], 'Module identity is missing')
        if 'Replace' in module:
            require(type(module['Replace']) is dict and type(module['Replace'].get('Path')) is str
                    and module['Replace']['Path'], 'Module replacement identity is missing')
        require(module['Path'] != MODULE and module.get('Replace', {}).get('Path') != MODULE,
                'Tempo module or replacement is compiled into main')
        identity = {key: module.get(key) for key in ('Path', 'Version', 'Sum', 'Main', 'Dir')}
        if module.get('Replace'):
            identity['Replace'] = {key: module['Replace'].get(key) for key in ('Path', 'Version', 'Sum')}
        require(module['Path'] not in modules or modules[module['Path']] == identity, 'Ambiguous production module metadata')
        modules[module['Path']] = identity
    require(packages and imports.issubset(packages), 'Production package closure is incomplete')
    native, settings = native_modules(native_text)
    require(MODULE not in native and all(v.get('Replace', {}).get('Path') != MODULE for v in native.values()),
            'Tempo is present in native modules')
    require(set(modules) == set(native), 'Full graph/native module sets differ')
    local = []
    for path, module in modules.items():
        if module.get('Main') is True:
            require(module.get('Version') is None and module.get('Sum') is None and 'Replace' not in module,
                    'Workspace module has unexpected external identity')
            require(path == 'github.com/grafana/grafana' or native[path]['Version'] == '(devel)',
                    'Native workspace version differs')
            require(not native[path].get('Replace') and not native[path]['Sum'], 'Native workspace identity differs')
            require(type(module.get('Dir')) is str and (module['Dir'] == '/source' or module['Dir'].startswith('/source/')),
                    'Workspace module escapes reviewed source tree')
            local.append(module['Dir'].removeprefix('/source') or '/')
        else:
            require(effective(module) == effective(native[path]), 'Effective native path/version/sum differs')
    require(settings.get('CGO_ENABLED') == '0' and settings.get('GOOS') == 'linux'
            and settings.get('GOARCH') == 'amd64' and settings.get('GOAMD64') == 'v1'
            and settings.get('vcs.revision') == 'af4a33e3786594ad72f72d97e6744010330c2977'
            and settings.get('vcs.modified') == 'true', 'Actual native build settings differ')
    return {'packages': len(packages), 'modules': len(modules), 'external_modules': len(modules) - len(local),
            'workspace_directories': sorted(local), 'tempo_packages': 0, 'tempo_native_modules': 0,
            'complete_effective_dependencies_equal': True}


def load_review(root, now):
    path = checked(root, REVIEW_PATH)
    require(old.file_hash(path) == REVIEW_SHA256, 'Independent candidate source2 review bytes changed')
    review = old.json_loads(path.read_bytes())
    require(review['schema'] == 'grafana-candidate-source2-technical-applicability-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['review_kind'] == 'technical_applicability' and review['facts_passed'] is True
            and review['human_risk_acceptance'] is False and review['artifact_approved'] is False
            and review['old_human_authorization_inherited'] is False, 'No independent exact technical review')
    try:
        validate_review_policy(root, REVIEW_PATH, now=now)
    except ValueError as error:
        raise old.DispositionError(str(error)) from None
    require(review['source']['path'] == SOURCE and review['source']['module'] == MODULE
            and review['source']['declared_version'] == VERSION, 'Reviewed source tuple differs')
    require(review['source']['rows'] == sorted(IDS), 'Reviewed advisory set differs')
    for relative, sha in (review['evidence_files'] | review['input_files']).items():
        require(old.file_hash(checked(root, relative)) == sha, 'Reviewed evidence or actual build input changed')
    subject = review['subject']
    integrity = old.json_loads(checked(root, EVIDENCE + 'whole-oci-integrity.json').read_bytes())
    for key in ('index_digest', 'platform_manifest_digest', 'config_digest', 'archive_sha256', 'binary_sha256'):
        require(subject[key] == integrity[key], 'Complete OCI integrity subject differs')
    require(integrity['authorization_evaluated'] is False and integrity['verified_blobs'] == 33
            and integrity['recomputed_diff_ids'] == subject['diff_ids'], 'Complete OCI integrity proof differs')
    require(subject['root_layout'] == 'direct_manifest', 'Reviewed OCI root layout differs')
    graph = review['graph']
    with gzip.open(checked(root, graph['path']), 'rb') as stream:
        raw = stream.read(graph['raw_bytes'] + 1)
    require(len(raw) == graph['raw_bytes'] and digest(raw) == graph['raw_sha256'], 'Full production graph bytes differ')
    native = checked(root, EVIDENCE + 'main-actual-native-buildinfo.txt').read_text(encoding='utf-8')
    stage = checked(root, EVIDENCE + 'main-build-stage-buildinfo.txt').read_text(encoding='utf-8')
    require(native.splitlines()[1:] == stage.splitlines()[1:], 'Actual and build-stage native module records differ')
    require(graph_facts(raw, native) == graph['verified_facts'], 'Full production graph/native facts differ')
    native_json = old.json_loads(checked(root, EVIDENCE + 'main-actual-native-buildinfo.json').read_bytes())
    native_map, native_settings = native_modules(native)
    json_map = {m['Path']: {k: m.get(k, '') for k in ('Path', 'Version', 'Sum')}
                | ({'Replace': {k: m['Replace'].get(k, '') for k in ('Path', 'Version', 'Sum')}}
                   if m.get('Replace') else {}) for m in [native_json['Main']] + native_json['Deps']}
    require(native_json['GoVersion'] == 'go1.27.3' and native_json['Path'] == 'github.com/grafana/grafana/pkg/cmd/grafana'
            and json_map == native_map and {v['Key']: v['Value'] for v in native_json['Settings']} == native_settings,
            'Independently extracted actual ELF native JSON differs from image native text')
    extraction = old.json_loads(checked(root, EVIDENCE + 'candidate-main-extraction-receipt.json').read_bytes())
    require(extraction['actor'] == '/root/frozen_security_audit' and extraction['authorization_evaluated'] is False
            and extraction['archive_sha256'] == subject['archive_sha256']
            and extraction['target'] == TARGET and extraction['elf_sha256'] == subject['binary_sha256'][TARGET]
            and extraction['native_json_sha256'] == old.file_hash(checked(root, EVIDENCE + 'main-actual-native-buildinfo.json')),
            'Independent actual main extraction identity differs')
    receipt = old.json_loads(checked(root, EVIDENCE + 'main-graph-receipt.json').read_bytes())
    require(receipt['completed'] is True and receipt['exit_code'] == 0
            and receipt['raw_graph_modified_or_filtered'] is False and receipt['raw_sha256'] == graph['raw_sha256']
            and receipt['raw_bytes'] == graph['raw_bytes'] and receipt['package_count'] == graph['verified_facts']['packages']
            and receipt['module_count'] == graph['verified_facts']['modules']
            and receipt['command'] == ['go', 'list', '-deps', '-json', '-mod=readonly', './pkg/cmd/grafana'],
            'Complete production graph collection differs')
    require(receipt['schema'] == 'grafana-candidate-complete-production-graph-v1'
            and receipt['compiler'] == 'go version go1.27.3 linux/amd64'
            and receipt['go_environment'] == {'CGO_ENABLED': '0', 'GOAMD64': 'v1', 'GOARCH': 'amd64',
                'GOFLAGS': '-p=2', 'GOOS': 'linux', 'GOWORK': '/source/go.work'}, 'Production compiler environment differs')
    for name, sha in receipt['actual_lock_sha256'].items():
        require(name in ('go.mod', 'go.sum', 'go.work', 'go.work.sum')
                and old.file_hash(checked(root, EVIDENCE + 'main-actual-' + name)) == sha,
                'Actual compile lock differs from graph receipt')
    require(set(receipt['actual_lock_sha256']) == {'go.mod', 'go.sum', 'go.work', 'go.work.sum'}
            and receipt['actual_lock_sha256']['go.mod'] == review['input_files'][SOURCE], 'Main actual manifest differs')
    materials = old.json_loads(checked(root, EVIDENCE + 'candidate-materials.json').read_bytes())
    main = materials['native_elfs'][TARGET]
    require(materials['actual_image_id'] == subject['platform_manifest_digest']
            and materials['archive_sha256'] == subject['archive_sha256']
            and materials['archive_bytes'] == subject['archive_bytes']
            and main['elf_sha256'] == subject['binary_sha256'][TARGET]
            and main['buildinfo_sha256'] == old.file_hash(checked(root, EVIDENCE + 'main-actual-native-buildinfo.txt')),
            'Actual candidate main/OCI/native collection differs')
    image = old.json_loads(checked(root, EVIDENCE + 'candidate-full-trivy.json').read_bytes())
    old.summarize(image)
    require(image['Metadata']['ImageID'] == subject['platform_manifest_digest']
            and image['Metadata']['DiffIDs'] == subject['diff_ids'], 'Original image scan targets another subject')
    main_rows = [r for r in image['Results'] if r['Target'] == TARGET]
    require(len(main_rows) == 1, 'Original main ELF scan target is missing or duplicated')
    inventory = old.inventory(main_rows[0]['Packages'])
    require(len(inventory) == review['main_inventory']['count']
            and digest(old.canonical(inventory)) == review['main_inventory']['sha256']
            and all(row[0] != MODULE for row in inventory), 'Actual main ELF inventory differs')
    return review


def apply_exact_two(original, scan, review):
    """The old result is immutable; additional findings remain unresolved."""
    result = deepcopy(original)
    require(original.get('scan_completion_verified') is True and 'error' not in original
            and original.get('technical_not_applicable_count') == 2, 'Old exact2 completion/review failed')
    scope = review['source']
    matches = [r for r in scan['Results'] if r['Target'] == SOURCE and r.get('Class') == 'lang-pkgs'
               and r.get('Type') == 'gomod']
    require(len(matches) == 1, 'New exact source manifest target is missing or duplicated')
    inventory = old.inventory(matches[0]['Packages'])
    require(len(inventory) == scope['inventory_count'] and digest(old.canonical(inventory)) == scope['inventory_sha256'],
            'New source manifest package inventory differs')
    required = Counter((advisory, SOURCE, MODULE, VERSION, 'HIGH') for advisory in IDS)
    selected, remaining, seen = [], [], Counter()
    for finding in original['unresolved']:
        key = tuple(finding.get(k) for k in ('id', 'target', 'package', 'version', 'severity'))
        if finding['kind'] == 'vulnerability' and key in required:
            seen[key] += 1
            selected.append({**finding, 'technical_disposition': DISPOSITION})
        else:
            remaining.append(finding)
    require(seen == required, 'New exact2 finding identity/count differs; missing or duplicate findings cannot pass')
    result.update(candidate_source2_technical_not_applicable=selected,
                  candidate_source2_technical_not_applicable_count=2,
                  technical_not_applicable_count=4,
                  technical_not_applicable=deepcopy(original['technical_not_applicable']) + selected,
                  unresolved=remaining, unresolved_count=len(remaining), passed=not remaining,
                  mode='independent_old_and_candidate_exact_source2' if not remaining else 'failed_closed',
                  artifact_approved=False, old_human_authorization_inherited=False,
                  original_exact2_verdict_preserved=True, candidate_review_sha256=REVIEW_SHA256)
    return result


def evaluate(scan, receipt, original_verdict, *, raw_sha256, source_commit, root=ROOT, now=None,
             tracked_clean=True, expected_targets=None):
    now = now or datetime.now(timezone.utc)
    result = {'passed': False, 'mode': 'failed_closed', 'candidate_source2_technical_not_applicable_count': 0,
              'raw_report_modified': False, 'artifact_approved': False, 'new_human_risk_acceptance': False}
    try:
        require(type(now) is datetime and now.tzinfo is not None, 'Invalid review clock')
        original = old.evaluate(scan, receipt, raw_sha256=raw_sha256, source_commit=source_commit, root=root,
                                now=now, tracked_clean=tracked_clean, expected_targets=expected_targets)
        require(original == original_verdict, 'Preserved old exact2 verdict differs from independently recomputed verdict')
        result['original_verdict_sha256'] = digest(old.canonical(original_verdict))
        review = load_review(root, now)
        updated = apply_exact_two(original, scan, review)
        result.update(updated)
    except (ValueError, KeyError, TypeError, OSError, EOFError, UnicodeError) as error:
        result['error'] = str(error) if isinstance(error, old.DispositionError) else 'Candidate source2 evidence is invalid'
        result['error_type'] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scan', 'scan-receipt', 'original-verdict', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    if (args.output.exists() or args.output.is_symlink()
            or not args.output.resolve().is_relative_to(ROOT.resolve() / '.cache')):
        print(json.dumps({'passed': False, 'error': 'Use a new diagnostic output under repository .cache'}))
        raise SystemExit(1)
    try:
        require(args.scan.is_file() and not args.scan.is_symlink()
                and 0 < args.scan.stat().st_size <= old.MAX_REPORT_BYTES, 'Full original raw source scan is missing or oversized')
        scan, receipt, original = [old.json_loads(p.read_bytes()) for p in (args.scan, args.scan_receipt, args.original_verdict)]
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
        result = evaluate(scan, receipt, original, raw_sha256=old.file_hash(args.scan), source_commit=head,
                          tracked_clean=not dirty, expected_targets=old.committed_manifest_targets(ROOT, head))
        result['preserved_original_verdict_file_sha256'] = old.file_hash(args.original_verdict)
    except (ValueError, OSError, subprocess.SubprocessError):
        result = {'passed': False, 'mode': 'failed_closed', 'error': 'Original scan, completion or verdict is unavailable'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('passed', 'mode', 'candidate_source2_technical_not_applicable_count',
          'unresolved_count', 'artifact_approved', 'error') if k in result}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
