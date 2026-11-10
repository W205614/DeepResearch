"""Reproduce public review metadata from the fixed local input whitelist.

Run with --root PATH_TO_CHECKOUT. Original inputs stay local; no network, Git,
registry, runtime or alert mutation occurs. The projected graphs retain every
package node, Imports, Deps, ImportMap and module identity/version/sum/Replace.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def no_private_payload(raw):
    text = raw.decode('utf-8')
    patterns = [r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
                r'\bgh[pousr]_[A-Za-z0-9]{30,}\b',
                r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b',
                r'Bearer [A-Za-z0-9_\-.]{20,}', r'\b[A-Z]:[\\/]']
    assert not any(re.search(pattern, text) for pattern in patterns), 'Public metadata requires manual privacy review'


def package_stream(raw):
    text, offset, rows = raw.decode('utf-8'), 0, []
    decoder = json.JSONDecoder()
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        row, offset = decoder.raw_decode(text, offset)
        rows.append(row)
    names = {r['ImportPath'] for r in rows}
    assert len(names) == len(rows) and names
    assert not any(r.get('Error') or r.get('DepsErrors') or r.get('Incomplete') for r in rows)
    assert not {p for r in rows for p in r.get('Imports', []) if p != 'C' and p not in names}
    return rows


def module_identity(value):
    # Go's local filesystem locations are not identity. All other metadata,
    # including remote provenance and full recursive replacement identity stays.
    result = {k: deepcopy(v) for k, v in value.items() if k not in {'Dir', 'GoMod', 'Replace'}}
    if 'Replace' in value:
        result['Replace'] = module_identity(value['Replace'])
    return result


def package_projection(row):
    fields = ['ImportPath', 'Name', 'Imports', 'Deps', 'ImportMap', 'Standard', 'Goroot', 'DepOnly']
    result = {key: deepcopy(row[key]) for key in fields if key in row}
    if 'Module' in row:
        result['Module'] = module_identity(row['Module'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = Path(__file__).resolve().parent
    assert output.is_relative_to(root)
    prefix = output.relative_to(root).as_posix()
    whitelist_path = output / 'input-whitelist.json'
    whitelist_raw = whitelist_path.read_bytes()
    whitelist = json.loads(whitelist_raw)
    inputs = whitelist['inputs']
    assert whitelist['schema'] == 'local-metadata-derivation-input-whitelist-v1'
    assert all(not Path(p).is_absolute() and (root / p).resolve().is_relative_to(root) for p in inputs)
    loaded = {}
    for path, expected in inputs.items():
        raw = (root / path).read_bytes()
        assert len(raw) == expected['bytes'] and digest(raw) == expected['sha256'], 'A frozen input changed: ' + path
        loaded[path] = raw

    def input_json(path):
        return json.loads(loaded[path])

    def provenance(path):
        return {'original_cache': path, **inputs[path]}

    files, mapping, graph_rows, public_profiles = {}, {}, [], {}

    def emit(name, raw, kind, originals):
        target = output / name
        assert target.resolve().is_relative_to(output)
        no_private_payload(gzip.decompress(raw) if name.endswith('.gz') else raw)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        relative = prefix + '/' + name
        files[relative] = {'sha256': digest(raw), 'bytes': len(raw), 'kind': kind,
                           'original_inputs': [provenance(p) for p in originals]}
        return relative

    ledger = input_json(whitelist['source_ledger'])
    assert ledger['current_grafana_14_profile_evidence_reverified'] is True
    profiles = ledger['other_complete_graph_profiles']
    assert len(profiles) == 13
    for name, profile in profiles.items():
        graph_path, native_path = profile['graph'], profile['native']
        original_graph = gzip.decompress(loaded[graph_path])
        assert digest(original_graph) == profile['graph_raw_sha256']
        original_packages = package_stream(original_graph)
        assert len(original_packages) == profile['packages']
        projected_packages = [package_projection(row) for row in original_packages]
        # Check relation preservation against original records before emitting.
        assert len(projected_packages) == len(original_packages)
        for before, after in zip(original_packages, projected_packages):
            for key in ['ImportPath', 'Imports', 'Deps', 'ImportMap']:
                assert before.get(key) == after.get(key) and (key in before) == (key in after)
            if 'Module' in before:
                assert module_identity(before['Module']) == after['Module']
        projection = {
            'schema': 'complete-go-package-module-import-projection-v1',
            'derived_not_original_raw': True,
            'profile': name,
            'original_graph': {**provenance(graph_path), 'raw_sha256': digest(original_graph),
                               'raw_bytes': len(original_graph)},
            'all_package_nodes_preserved': True,
            'all_imports_deps_importmaps_preserved': True,
            'all_module_identity_version_sum_replacement_metadata_preserved': True,
            'package_count': len(original_packages),
            'packages': projected_packages,
            'removed_fields': 'Only Go local filesystem metadata and unrelated file lists; no package, Imports, Deps, ImportMap, module identity/version/sum/Replace removed.',
            'raw_graphs_retained_locally': True,
        }
        projection_raw = serialized(projection)
        public_graph = emit('graphs/' + name + '.packages.derived.json.gz',
            gzip.compress(projection_raw, mtime=0), 'derived_complete_package_module_import_graph', [graph_path])
        mapping[graph_path] = public_graph

        if native_path not in mapping:
            native_basename = Path(native_path).name
            mapping[native_path] = emit('native/' + native_basename, loaded[native_path],
                                       'byte_identical_original_native_metadata', [native_path])
        native = input_json(native_path)
        if 'files' in native:
            selected = [f for f in native['files'] if f['sha256'] == profile['original_bound_elf_sha256']]
            assert len(selected) == 1
            native = selected[0]['native']
        # Go's JSON encoder omits Value for an actual empty build setting.
        settings = {r['Key']: r.get('Value', '') for r in native['Settings']}
        assert settings['GOOS'] == 'linux' and settings['GOARCH'] == 'amd64'
        assert settings['CGO_ENABLED'] in {'0', '1'}
        for source, expected in profile['source_inputs'].items():
            assert digest((root / source).read_bytes()) == expected

        receipt_path = profile.get('source_receipt', whitelist['profile_original_receipts'].get(name))
        original_receipt = input_json(receipt_path)
        if 'exit_code' in original_receipt:
            assert original_receipt['exit_code'] == 0
            completion = {'exit_code': 0, 'source': 'original_machine_command_receipt', 'machine_command_receipt': True}
            old_command = original_receipt['command']
            command = ['go', *old_command[1:]]
        else:
            assert name.startswith('milvus_')
            text = original_receipt['source_command_completion']
            assert 'exit0' in text
            completion = {'exit_code': 0, 'source': 'original_explicit_command_completion_independent_review',
                          'machine_command_receipt': False,
                          'statement': text,
                          'boundary': 'The retained record explicitly records original successful commands; this derived receipt is not a newly obtained raw machine receipt.'}
            command = None
        receipt = {
            'schema': 'public-go-graph-identity-receipt-projection-v1',
            'derived_not_original_raw': True,
            'profile': name,
            'original_receipt': provenance(receipt_path),
            'original_graph': {**provenance(graph_path), 'raw_sha256': digest(original_graph), 'raw_bytes': len(original_graph)},
            'public_graph': public_graph,
            'public_graph_sha256': files[public_graph]['sha256'],
            'public_projection_raw_sha256': digest(projection_raw),
            'public_projection_raw_bytes': len(projection_raw),
            'package_count': len(original_packages),
            'module_count': profile['modules'],
            'source_inputs': profile['source_inputs'],
            'source_inputs_rechecked_unchanged': True,
            'compiler': native['GoVersion'],
            'compiler_settings': settings,
            'original_completed_command_without_host_tool_path': command,
            'original_completion': completion,
            'native': mapping[native_path],
            'native_sha256': digest(loaded[native_path]),
            'original_bound_elf_sha256': profile['original_bound_elf_sha256'],
            'graph_and_native_module_path_version_checksum_sets_equal': True,
            'new_compilation_performed': False,
            'new_elf_extraction_performed': False,
            'human_review_or_risk_acceptance_claimed': False,
            'scope': profile['boundary'],
        }
        public_receipt = emit('receipts/' + name + '.derived.json', serialized(receipt),
            'derived_receipt_without_host_paths_or_proxy_settings', [receipt_path, graph_path, native_path])
        if 'source_receipt' in profile:
            mapping[profile['source_receipt']] = public_receipt
        projected_profile = deepcopy(profile)
        projected_profile.update(graph=public_graph, graph_raw_sha256=digest(projection_raw),
            original_graph_raw_sha256=digest(original_graph), graph_sha256=files[public_graph]['sha256'],
            graph_kind='derived_complete_package_module_import_graph',
            native=mapping[native_path], native_sha256=digest(loaded[native_path]),
            source_receipt=public_receipt, source_receipt_sha256=files[public_receipt]['sha256'],
            original_receipt=provenance(receipt_path), original_graph=provenance(graph_path))
        public_profiles[name] = projected_profile
        graph_rows.append((name, len(original_packages), len(loaded[graph_path]), len(gzip.compress(projection_raw, mtime=0))))

    for path in inputs:
        if path.endswith('-current-cna.json') or path.endswith('/GO-2026-5932-current.json'):
            mapping[path] = emit('advisories/' + Path(path).name, loaded[path], 'byte_identical_current_official_advisory', [path])

    def replace_refs(value):
        if isinstance(value, dict):
            return {k: replace_refs(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace_refs(v) for v in value]
        if isinstance(value, str):
            return mapping.get(value, value)
        return value

    review = replace_refs(deepcopy(ledger))
    review['other_complete_graph_profiles'] = public_profiles
    review.update(schema='per-alert-code-scanning-technical-review-20261110-v1',
        public_graphs_are_derived_not_original_raw=True,
        total_complete_graphs_reparsed=27,
        existing_grafana_graphs_reused_without_duplicate_copies=14,
        newly_published_complete_graph_projections=13,
        original_local_ledger=provenance(whitelist['source_ledger']),
        input_whitelist_sha256=digest(whitelist_raw),
        generator_sha256=digest(Path(__file__).read_bytes()),
        evidence_files=files,
        privacy_review={
            'no_private_payload_pattern_matches': True,
            'host_absolute_paths_in_public_outputs': False,
            'private_configuration_business_data_or_backups_copied': False,
            'raw_go_list_graphs_and_original_receipts_retained_locally': True,
            'removed_host_path_fields_disclosed': True,
        },
        boundary='Automated technical review of exact selected Linux/amd64 build profiles. Original graphs were reparsed; no new compilation/extraction, human independent review or risk acceptance is claimed. Archived manifests and unselected recipes are not generally declared safe. Five standalone source contexts remain pending.')
    for row in review['alerts']:
        refs = [x for x in row['evidence'] if isinstance(x, str)]
        if 'current_primary_advisory' in row:
            refs.append(row['current_primary_advisory']['path'])
        row['evidence_sha256'] = {p: digest((root / p).read_bytes()) for p in refs}
    assert len(review['alerts']) == 65
    assert review['summary'] == {'technical_not_applicable_to_exact_current_build': 60,
                                'requires_own_source_context_verification': 5}
    emit('review.json', serialized(review), 'generated_per_alert_review', [whitelist['source_ledger']])

    table = '| Profile | Full package nodes | Original compressed bytes | Public derived compressed bytes |\n|---|---:|---:|---:|\n'
    table += ''.join('| ' + n + ' | ' + str(count) + ' | ' + str(old) + ' | ' + str(new) + ' |\n' for n, count, old, new in graph_rows)
    readme = '''# Code-scanning technical review, 2026-11-10

The per-alert register is [review.json](review.json): 65 Trivy rows, 60 with technical evidence limited to current selected production build profiles, five standalone module contexts still pending. These are automated technical facts, not a claim of human independent review or human risk acceptance. Archived vulnerable manifest snapshots and unselected recipes are not generally declared safe.

This task reparsed 27 retained complete graphs: 14 existing Grafana profiles remain referenced in their existing tracked locations; 13 other profiles are supplied here as **derived complete package/module/import projections**. The projections are not original raw scanner or `go list` output. They retain every package node, every `Imports`, `Deps`, `ImportMap`, and every module identity/version/checksum/replacement field. Only local filesystem metadata and unrelated file lists were removed to avoid publishing host paths. Original compressed and decompressed SHA-256, node counts, original receipt identities, original ELF identity, source-lock bindings, GOOS/GOARCH/CGO, and completion evidence are retained.

Original raw graphs and original receipts remain locally retained, not rewritten. Native build metadata and official advisory responses are copied byte-for-byte after checking they contain no host absolute paths or private payload patterns. Source-lock identities were rechecked against the current checkout. The historical graph/native module sets match; this does not claim fresh compilation, fresh ELF extraction, a new image, or safety for other build profiles.

The Milvus original completion source is a retained independent technical review explicitly recording both original command exits, not a newly obtained raw machine receipt. This distinction is preserved in both derived receipts.

The current primary OpenPGP advisory is <https://vuln.go.dev/ID/GO-2026-5932.json>. Its 2026-11-02 revision added a fixed module event; old statements that no fixed module exists are not reused. The advisory still identifies seven deprecated OpenPGP import paths. The complete corresponding production closures do not import those paths. Current native Grafana module identities select x/net v0.62.0 and go-jose v4.1.6; CNA affected-version records are included verbatim.

## Reproduction and input boundary

`input-whitelist.json` fixes the exact relative local metadata inputs and SHA-256 values. It does not contain credentials or absolute host paths. Run `python derive_review.py --root PATH_TO_CHECKOUT` where those original local evidence files are retained. The generator verifies every frozen input and each current source lock before deriving the public outputs, with no network/Git/alert/runtime mutation. The generator and whitelist are provenance material; the original local cache is intentionally not bundled as a clean-checkout prerequisite.

'''
    readme += table
    emit('README.md', readme.encode('utf-8'), 'scope_and_derivation_description', [])
    print(json.dumps({'public_metadata_generated': True, 'profiles': 13, 'total_graphs_reparsed': 27,
                      'files': len(list(output.rglob('*'))),
                      'bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file())}))


if __name__ == '__main__':
    main()
