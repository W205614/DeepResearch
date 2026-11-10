"""Derive public metadata for four isolated source probes; never run or mutate them."""
import argparse
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = Path(__file__).resolve().parent
    local = root / '.cache/security-alert-review-20261110/go-probes/four-closure-evidence'
    source = (local / 'review.json').read_bytes()
    review = json.loads(source)
    assert {r['alert'] for r in review['rows']} == {14, 22, 23, 48}
    spec = importlib.util.spec_from_file_location('public_metadata', output.parent / 'derive_review.py')
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    for row in review['rows']:
        target = root / row['target']
        assert sha(target.read_bytes()) == row['target_sha256']
        assert sha(target.with_name('go.sum').read_bytes()) == row['original_go_sum_sha256']
        component = 'etcd' if row['alert'] in {14, 22, 23} else 'milvus'
        assert sha((root / ('deployment/vendors/' + component + '/source.lock.json')).read_bytes()) == row['source_lock_sha256']
        original = row.pop('full_unmodified_go_list_output')
        compressed = (local / original['file']).read_bytes()
        raw = gzip.decompress(compressed)
        assert sha(compressed) == original['gzip_sha256'] and len(compressed) == original['gzip_bytes']
        assert sha(raw) == original['raw_sha256'] and len(raw) == original['raw_bytes']
        text, offset, nodes = raw.decode(), 0, []
        decoder = json.JSONDecoder()
        while offset < len(text):
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset == len(text):
                break
            node, offset = decoder.raw_decode(text, offset)
            assert not node.get('Error') and not node.get('DepsErrors') and not node.get('Incomplete')
            projected = helpers.package_projection(node)
            if 'ForTest' in node:
                projected['ForTest'] = deepcopy(node['ForTest'])
            nodes.append(projected)
        assert len(nodes) == row['graph_packages'] and row['exit_code'] == 0 and row['graph_error_count'] == 0
        assert len({n['ImportPath'] for n in nodes}) == len(nodes)
        assert not [n for n in nodes if n['ImportPath'].split(' [')[0].startswith('golang.org/x/crypto/openpgp')]
        if row['alert'] == 23:
            assert not [n for n in nodes if n['ImportPath'].startswith('go.mongodb.org/mongo-driver/x/mongo/driver/auth')]
        data = (json.dumps({'schema': 'complete-go-package-projection-v1', 'original_raw_sha256': sha(raw),
                           'original_raw_bytes': len(raw), 'package_count': len(nodes), 'packages': nodes},
                          ensure_ascii=False, indent=2) + '\n').encode()
        helpers.no_private_payload(data)
        name = str(row['alert']) + '-packages.derived.json.gz'
        payload = gzip.compress(data, mtime=0)
        (output / name).write_bytes(payload)
        row['original_raw_retained_locally_not_published'] = original
        row['complete_public_projection'] = {'file': name, 'sha256': sha(payload), 'bytes': len(payload),
                                             'raw_sha256': sha(data), 'raw_bytes': len(data)}
    review['derived_not_original_raw'] = True
    review['original_local_review_sha256'] = sha(source)
    review['privacy_boundary'] = 'Every package, import/dependency/import-map and module identity remains. Host directories/file lists are omitted. Original unfiltered graphs and first failed probes remain local.'
    serialized = (json.dumps(review, ensure_ascii=False, indent=2) + '\n').encode()
    helpers.no_private_payload(serialized)
    (output / 'review.json').write_bytes(serialized)
    continuity = (root / '.cache/security-alert-review-20261110/source-continuity.json').read_bytes()
    helpers.no_private_payload(continuity)
    (output / 'source-continuity.json').write_bytes(continuity)
    print(json.dumps({'derived_graphs': 4, 'scope': 'exact isolated sources after same-version checksum completion',
                      'source_continuity_sha256': sha(continuity)}))


if __name__ == '__main__':
    main()
