"""Publish the fixed isolated telemetry example proof, without publishing binaries."""
import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stream(raw):
    text, offset, rows = raw.decode(), 0, []
    decoder = json.JSONDecoder()
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        row, offset = decoder.raw_decode(text, offset)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = Path(__file__).resolve().parent
    local = root / '.cache/security-alert-review-20261110/go-probes/telemetry-maintained-v3-fixed-candidate'
    raw = (local / 'handoff.json').read_bytes()
    record = json.loads(raw)
    assert record['status'] == 'passed' and record['alert'] == 35
    assert record['source_commit'] == '328df0cd2f323c2d3c50cef91bfc9f2c412bcb09'
    spec = importlib.util.spec_from_file_location('public_metadata', output.parent / 'derive_review.py')
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    patch = (local / record['patch']['file']).read_bytes()
    assert digest(patch) == record['patch']['sha256'] and len(patch) == record['patch']['bytes']
    assert record['patch']['git_apply_exact_original_successful'] is True
    (output / 'telemetry-maintained.patch').write_bytes(patch)
    fixtures = root / 'tests/fixtures/milvus-telemetry-example-20261110'
    fixtures.mkdir(parents=True, exist_ok=True)
    for name, expected in record['original_files'].items():
        content = (local / 'original-inputs' / name).read_bytes()
        assert digest(content) == expected
        if name.endswith('.go'):
            (fixtures / (name + '.txt')).write_bytes(content)
        else:
            assert digest((root / 'deployment/vendors/milvus/modules/examples/telemetry_demo' / name).read_bytes()) == expected
    for name, expected in record['local_client_pkg_source']['locks_sha256'].items():
        assert digest((root / name).read_bytes()) == expected
    original_graph = record['package_graph']
    compressed = (local / original_graph['file']).read_bytes()
    graph = gzip.decompress(compressed)
    assert digest(compressed) == original_graph['gzip_sha256'] and digest(graph) == original_graph['raw_sha256']
    nodes = stream(graph)
    assert len(nodes) == original_graph['packages'] == 430 and not any(n.get('Error') or n.get('DepsErrors') for n in nodes)
    assert not [n for n in nodes if n['ImportPath'].startswith('golang.org/x/crypto')]
    projected = (json.dumps({'schema': 'complete-go-package-projection-v1', 'packages': [helpers.package_projection(n) for n in nodes],
                            'original_raw_sha256': digest(graph), 'package_count': len(nodes)}, indent=2) + '\n').encode()
    helpers.no_private_payload(projected)
    payload = gzip.compress(projected, mtime=0)
    (output / 'packages.derived.json.gz').write_bytes(payload)
    record['original_package_graph_retained_locally'] = copy.deepcopy(original_graph)
    record['package_graph']['file'] = 'packages.derived.json.gz'
    record['package_graph']['public_derived_sha256'] = digest(payload)
    record['package_graph']['public_derived_bytes'] = len(payload)
    modules_raw = (local / 'module-graph.stdout.log').read_bytes()
    assert digest(modules_raw) == record['full_module_graph']['checks'][0]['stdout_sha256']
    modules = stream(modules_raw)
    assert len(modules) == record['full_module_graph']['selected_modules'] == 224
    crypto = [m for m in modules if m['Path'] == 'golang.org/x/crypto']
    assert len(crypto) == 1 and crypto[0]['Version'] == record['full_module_graph']['selected_xcrypto']
    selected = (json.dumps({'schema': 'selected-go-modules-projection-v1', 'original_raw_sha256': digest(modules_raw),
                            'modules': [helpers.module_identity(m) for m in modules]}, indent=2) + '\n').encode()
    helpers.no_private_payload(selected)
    (output / 'selected-modules.derived.json').write_bytes(selected)
    record['full_module_graph']['public_projection'] = {'file': 'selected-modules.derived.json', 'sha256': digest(selected)}
    for check in [*record['checks'], *record['full_module_graph']['checks']]:
        assert check['exit_code'] == 0
        check['command'] = ['go', *[Path(value).name if re.match(r'^[A-Za-z]:[\\/]', value) else value
                                   for value in check['command'][1:]]]
    record.update(derived_public_receipt_not_original=True, original_local_handoff_sha256=digest(raw),
                  binary_payloads_published=False,
                  binary_boundary='Three cross-compiled ELF identities and successful compile receipts only. No telemetry application, Milvus API or test case execution is claimed.')
    serialized = (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode()
    helpers.no_private_payload(serialized)
    (output / 'review.json').write_bytes(serialized)
    print(json.dumps({'patch_sha256': digest(patch), 'complete_packages': len(nodes), 'selected_modules': len(modules),
                      'selected_xcrypto': crypto[0]['Version'], 'source_fixtures': 3}))


if __name__ == '__main__':
    main()
