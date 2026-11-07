"""Exact source18 applicability for the new final subject; old authority is never reused."""
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
from scripts import check_grafana_release_20261107 as api  # noqa: E402
from scripts import check_grafana_active_source_retirement as original  # noqa: E402
from scripts import grafana_source_candidate4 as source4  # noqa: E402

MAX_RAW_BYTES = 128 * 1024**2
PROM_MODULE = 'github.com/grafana/grafana-prometheus-datasource/pkg/promlib'
PROM_COMMIT = '7d5bbc86bd38f95ca5143c8490d1204787cc391b'
PROM_TREE = '040000 tree 6dd189a97f1b9f388049740ccfc1fd9e0fce1eb8\tpkg/promlib'
PROM_LOCKS = {'root_go_mod': '6da55c5f45a0a1354e734be0c3da32765a17632584cb01f90ec8fe9b6e093258',
              'nested_go_mod': '5dbf5dfd95389517677695ae3fe5624f80597521a050a66daa62ef3b2d910947',
              'nested_go_sum': 'd678659fe0b0375a619a424f884e83812817fd3294bb17f8239259b55e21e976'}


def row_counter(rows):
    return Counter(tuple(row[k] for k in ('id', 'target', 'package', 'version', 'severity')) for row in rows)


def comparison_native(root, binding, profile, native, receipt):
    """Preserve raw evidence; only one proved Go metadata spelling is equivalent."""
    result = deepcopy(native)
    if profile['name'] != 'prometheus':
        return result
    equivalence = binding['inventory_identity']['exact_local_graph_native_equivalence']
    api.require(len(equivalence) == 1, 'Only one independently reviewed local representation is supported')
    proof = equivalence[0]
    api.require(proof['backend'] == 'prometheus' and proof['module'] == PROM_MODULE
        and proof['localpath'] == './pkg/promlib' and proof['upstream_commit'] == PROM_COMMIT
        and proof['original_git_subtree'] == PROM_TREE
        and proof['native'] == profile['native'] and proof['graph_receipt'] == profile['graph_receipt']
        and proof['native_replace'] == {'Path': './pkg/promlib', 'Version': '(devel)'}
        and proof['graph_replace'].get('Path') == './pkg/promlib'
        and proof['graph_replace'].get('Version') is None and proof['graph_replace'].get('Sum') is None,
        'Prometheus local native/graph identity escaped the exact reviewed scope')
    for key, expected in PROM_LOCKS.items():
        descriptor = proof[key]
        api.require(descriptor['sha256'] == expected and api.file_hash(root / descriptor['path']) == expected,
                    'Prometheus exact original/nested source locks changed')
    provenance = api.read_json(root / proof['source_provenance'])
    api.require(provenance['actual_git_revision'] == PROM_COMMIT
        and provenance['git_revision_verified_after_fetch_and_reset'] is True
        and provenance['nested_git_subtrees'] == {'pkg/promlib': PROM_TREE}
        and receipt['nested_module_original_git_subtrees'] == {'pkg/promlib': PROM_TREE}
        and receipt['actual_lock_sha256']['go.mod'] == PROM_LOCKS['root_go_mod']
        and receipt['actual_lock_sha256']['pkg/promlib/go.mod'] == PROM_LOCKS['nested_go_mod']
        and receipt['actual_lock_sha256']['pkg/promlib/go.sum'] == PROM_LOCKS['nested_go_sum'],
        'Prometheus original Git tree/actual lock receipt changed')
    deps = [dep for dep in result['Deps'] if dep['Path'] == PROM_MODULE]
    api.require(len(deps) == 1 and deps[0].get('Replace') == proof['native_replace'],
                'Actual native replacement differs from the original Go metadata')
    # Go1.27.3 formats empty local ModulePublic.Version as (devel). This one
    # comparison projection is not a rewritten native file, scan or SBOM.
    deps[0]['Replace'].pop('Version')
    return result


def verify_profiles(root, request, scope):
    binding = request['binding']
    profiles = scope['compiled_profiles']
    api.require(scope['subject'] == api.subject_for(binding) and len(profiles) == 14
        and {p['target'] for p in profiles} == set(binding['binary_sha256']),
        'Every compiled profile must bind this exact final fourteen-ELF subject')
    facts, names_seen = {}, set()
    inventory = {m['plugin_directory']: m for m in api.read_json(root / 'deployment/vendors/grafana-20261107/plugin-modules.json')}
    for profile in profiles:
        name, target = profile['name'], profile['target']
        api.require(name not in names_seen and (name == 'main' or name in inventory)
            and profile['elf_sha256'] == binding['binary_sha256'][target], 'Repeated or different-subject compiled profile')
        names_seen.add(name)
        for field in ('native', 'graph', 'graph_receipt'):
            path = profile[field]
            api.require(path in request['files_sha256']
                and api.file_hash(root / path) == request['files_sha256'][path], 'Full compiled profile evidence changed')
        native = api.read_json(root / profile['native'])
        receipt = api.read_json(root / profile['graph_receipt'])
        api.require(receipt['completed'] is True and receipt['exit_code'] == 0
            and receipt['raw_graph_modified_or_filtered'] is False
            and api.file_hash(root / profile['graph']) == receipt['compressed_sha256'], 'Compiled graph receipt is incomplete')
        with gzip.open(root / profile['graph'], 'rb') as stream:
            raw = stream.read(MAX_RAW_BYTES + 1)
        api.require(len(raw) == receipt['raw_bytes'] <= MAX_RAW_BYTES
            and hashlib.sha256(raw).hexdigest() == receipt['raw_sha256'], 'Complete raw graph bytes changed')
        projected = comparison_native(root, binding, profile, native, receipt)
        packages, modules, actual = source4.graph_facts(original, raw, projected)
        api.require(len(packages) == receipt['package_count'] and len(modules) == receipt['module_count'],
                    'Complete graph/native package/module coverage differs')
        settings = {s['Key']: s['Value'] for s in native['Settings']}
        source = 'deployment/vendors/grafana-20261107/' + ('main/go.mod' if name == 'main' else 'plugins/' + name + '/go.mod')
        expected_vcs = 'ce471d869a4810cd83c987dd3521c4b67689dbb6' if name == 'main' else inventory[name]['commit']
        tags = '' if name == 'main' else 'arrow_json_stdlib'
        api.require(len(settings) == len(native['Settings']) and native['GoVersion'] == 'go1.27.3'
            and settings['CGO_ENABLED'] == '0' and settings['GOOS'] == 'linux' and settings['GOARCH'] == 'amd64'
            and settings['GOAMD64'] == 'v1' and settings['vcs.revision'] == expected_vcs
            and settings['vcs.modified'] == 'true' and settings.get('-tags', '') == tags
            and receipt['build_tags'] == (tags or None)
            and receipt['actual_lock_sha256']['go.mod'] == binding['recipe_files'][source],
            'Actual compiled source/VCS/platform/tags differ')
        facts[name] = {'target': target, 'elf_sha256': profile['elf_sha256'], 'native': actual,
                       'packages': sorted(packages), 'package_count': len(packages), 'module_count': len(modules)}
    api.require(names_seen == {'main', *inventory}, 'All actual backend profiles are required')
    return facts


def source_rows():
    return Counter([original.SCOPE]
        + [(id, path, original.source.MODULE, original.source.VERSION, 'HIGH')
           for path in (original.OLD_SOURCE, original.source.SOURCE) for id in sorted(original.source.IDS)]
        + [('CVE-2026-78661', path, original.HTML_MODULE, 'v0.60.0', 'HIGH') for path in original.HTML_SOURCES]
        + [(id, path, source4.TEMPO, version, 'HIGH') for path, (_, version) in source4.SOURCES.items() for id in source4.IDS])


def assess_source(scan, scope, compiled):
    findings, counts, secrets = original.source.old.summarize(scan)
    api.require(not any(secrets.values()), 'Every source secret blocks')
    expected = source_rows()
    observed = row_counter([r for r in findings if r['severity'] in original.source.old.BASELINE_GATE_SEVERITIES])
    scoped = row_counter([r for r in findings if r['id'] in {key[0] for key in expected}])
    api.require(observed == scoped == row_counter(scope['rows']) == expected and sum(expected.values()) == 18,
                'Exact source18 tuples are missing, extra, duplicate or reclassified')
    targets = sorted([r['Target'], r.get('Class', ''), r.get('Type', '')] for r in scan['Results'])
    api.require(targets == scope['scan_targets'] and len(targets) == 62, 'Full current source coverage differs')
    for row in findings:
        if row['severity'] not in original.source.old.BASELINE_GATE_SEVERITIES:
            continue
        if row['id'] == original.SCOPE[0]:
            dependency = compiled['main']['native']['github.com/go-jose/go-jose/v4']
            api.require(dependency.get('Version') == 'v4.1.6' and not dependency.get('Replace'), 'Actual fixed jose source differs')
        elif row['id'] == 'CVE-2026-78661':
            name = 'main' if row['target'] == original.OLD_SOURCE else row['target'].split('/plugins/')[1].split('/')[0]
            dependency = compiled[name]['native']['golang.org/x/net']
            api.require(dependency.get('Version') == 'v0.62.0' and not dependency.get('Replace'), 'Actual fixed HTML module differs')
        else:
            name = 'tempo' if row['target'] == source4.PREFIX + 'plugins/tempo/go.mod' else 'main'
            fact = compiled[name]
            affected = [p for p in fact['packages'] if any(p == prefix or p.startswith(prefix + '/') for prefix in source4.AFFECTED)]
            api.require(not affected, 'An affected Tempo server package is compiled')
            if name == 'main':
                api.require(source4.TEMPO not in fact['native']
                    and not any(p == source4.TEMPO or p.startswith(source4.TEMPO + '/') for p in fact['packages']),
                    'Tempo is compiled into actual final main')
            else:
                dependency = fact['native'][source4.TEMPO]
                api.require(dependency == {'Path': source4.TEMPO,
                    'Version': 'v1.5.1-0.20260910130453-bcfe9f230c1d',
                    'Sum': 'h1:BphMCaYqWY+j1fdRPNFPkYE4A4QIev1KlHjRkSVJ8O4='}, 'Actual Tempo dependency differs')
    return {'technical_ready': True, 'passed': False, 'approval_pending': True, 'raw_report_modified': False,
            'raw_blocking_count': 18, 'full_source_targets': 62, 'raw_counts': counts,
            'compiled_profile_count': 14, 'actual_elf_sha256': {n: f['elf_sha256'] for n, f in compiled.items()},
            'unknown_dispositions_granted': 0, 'old_authorization_inherited': False, 'artifact_approved': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scan', 'scan-receipt', 'original-verdict', 'candidate-verdict', 'runtime-inventory', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args(argv)
    try:
        api.require(not args.output.exists() and args.output.resolve().is_relative_to((ROOT / '.cache/eval').resolve()), 'Use a new source output under .cache/eval')
        request = api.load_request(ROOT)
        scope_path = request['binding']['source_scope_file']
        api.require(scope_path in request['files_sha256'], 'New source scope is outside the reviewed request')
        scope = api.read_json(ROOT / scope_path)
        scan, receipt, old, candidate, inventory = [api.read_json(getattr(args, field)) for field in
            ('scan', 'scan_receipt', 'original_verdict', 'candidate_verdict', 'runtime_inventory')]
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True)
        prior = original.source.evaluate(scan, receipt, old, root=ROOT, source_commit=head,
            raw_sha256=api.file_hash(args.scan), now=datetime.now(timezone.utc), tracked_clean=not dirty,
            expected_targets=original.source.old.committed_manifest_targets(ROOT, head))
        api.require({k: v for k, v in candidate.items() if k != 'preserved_original_verdict_file_sha256'} == prior
            and candidate['preserved_original_verdict_file_sha256'] == api.file_hash(args.original_verdict),
            'Original complete source failures differ from independent recomputation')
        result = assess_source(scan, scope, verify_profiles(ROOT, request, scope))
        result['preserved_original_failed_verdict'] = old
        result['preserved_candidate_failed_verdict'] = candidate
        result['preserved_old_selection_failed_verdict'] = original.evaluate(scan, receipt, old, candidate, inventory,
            original_file_sha256=api.file_hash(args.original_verdict), raw_sha256=api.file_hash(args.scan),
            source_commit=head, root=ROOT, tracked_clean=not dirty,
            expected_targets=original.source.old.committed_manifest_targets(ROOT, head), profile='maintenance')
        if not args.prepare:
            api.validate_authorization(api.read_json(ROOT / api.AUTH_PATH), request)
            from scripts import runtime_ci_inventory  # noqa: E402
            api.require(inventory == runtime_ci_inventory.build_record(ROOT)
                and inventory['grafana_selection']['profile'] == api.PROFILE
                and inventory['grafana_selection']['subject'] == api.subject_for(request['binding']),
                'Current independent selector inventory does not select the newly authorized subject')
            api.require(scope['planned_selector_sha256'] and all(api.file_hash(ROOT / n) == s
                for n, s in scope['planned_selector_sha256'].items()), 'Actual selection differs from the reviewed activation patch')
            api.verify_published_delivery(ROOT, request)
            result.update(passed=True, approval_pending=False, mode='new_exact_source18_selection_verified')
    except (ValueError, TypeError, KeyError, OSError, EOFError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'mode': 'new_source_failed_closed', 'error_type': type(error).__name__,
                  'artifact_approved': False, 'error': str(error) if isinstance(error, ValueError) else 'Exact new source evidence unavailable'}
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT / '.cache/eval').resolve()):
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps({k: result[k] for k in ('passed', 'technical_ready', 'approval_pending', 'error') if k in result}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
