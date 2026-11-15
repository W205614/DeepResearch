"""Prepare exact Tempo exemplar-limit source facts; never approve the resulting draft."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_tempo_app_exact1 as high  # noqa: E402
from scripts.source_dependency_migration_20261115 import load_current_context  # noqa: E402

CURRENT = 'f317720e7e5191a7f282e8f7d6fe5387ae6015b4'
BACKPORT = 'b481ae9693f99785691197915066e6306950fa09'
EVIDENCE = 'deployment/security/tempo-all-severity-20261115-evidence'
FILES = {'modules/frontend/metrics_query_range_handler.go': 'github.com/grafana/tempo/modules/frontend',
         'modules/frontend/metrics_query_range_sharder.go': 'github.com/grafana/tempo/modules/frontend',
         'pkg/traceql/engine_metrics.go': 'github.com/grafana/tempo/pkg/traceql'}
REGRESSIONS = ('TestCompileMetricsQueryRangeExemplarsSafetyCap',
               'TestCompileMetricsQueryRangeNonRawExemplarsSafetyCap',
               'TestNormalizeRequestExemplars', 'TestQueryRangeHandlerExemplarNormalization')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(args):
    old = high.load_review()
    context, packages = load_current_context('tempo')
    if context['source_provenance']['Origin']['Hash'] != CURRENT:
        raise ValueError('Tempo current compilation is not the reviewed source commit')
    raw = args.raw_context.read_bytes()
    if len(raw) != context['raw_bytes'] or hashlib.sha256(raw).hexdigest() != context['raw_sha256']:
        raise ValueError('Complete original Go context does not match the independently pinned receipt')
    decoder, position, complete = json.JSONDecoder(), 0, {}
    text = raw.decode()
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        row, position = decoder.raw_decode(text, position)
        name = row['ImportPath']
        if name in complete or row.get('Error') or row.get('DepsErrors') or row.get('Incomplete'):
            raise ValueError('Incomplete or duplicate original Go context')
        complete[name] = row
    if complete.keys() != packages.keys():
        raise ValueError('Complete original Go context differs from the pinned projection')
    evidence = ROOT / EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)
    hashes = {}

    def copy(source, relative, compressed=False):
        destination = evidence / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(gzip.compress(source.read_bytes(), mtime=0) if compressed else source.read_bytes())
        name = destination.relative_to(ROOT).as_posix()
        hashes[name] = digest(destination)
        return name

    compare_path = args.source_dir / 'compare-backport-to-runtime.api-response.json'
    compare = json.loads(compare_path.read_bytes())
    if (compare['status'] != 'ahead' or compare['behind_by'] != 0
            or compare['base_commit']['sha'] != BACKPORT or compare['merge_base_commit']['sha'] != BACKPORT
            or compare['url'] != 'https://api.github.com/repos/grafana/tempo/compare/' + BACKPORT + '...' + CURRENT
            or compare['html_url'] != 'https://github.com/grafana/tempo/compare/' + BACKPORT + '...' + CURRENT):
        raise ValueError('Official stable fix is not an ancestor of the actual source commit')
    ancestry = copy(compare_path, 'backport-ancestry.api-response.json.gz', True)
    original_context = copy(args.raw_context, 'complete-tempo-go-context.json.gz', True)
    copy(args.source_dir / 'CVE-2026-27878.cna.json', 'CVE-2026-27878.cna.json')
    for commit in (BACKPORT, 'b13f74291d489672601a10297f8fbcbf7dd19192',
                   '3d7c78d438890991df594c20ae2031f8934aba3b', 'e2d51b786aff94de3319c07994c6a5539b121eb5'):
        source = args.source_dir / (commit + '.api-response.json')
        if json.loads(source.read_bytes())['sha'] != commit:
            raise ValueError('Official fix commit identity changed')
        copy(source, 'fix-commits/' + source.name + '.gz', True)
    ast_tool = args.source_dir / 'compare_go_ast.exe'
    copy(args.source_dir / 'compare_go_ast.go', 'compare_go_ast.go')
    sources = {}
    for name, package in FILES.items():
        if package not in packages or Path(name).name not in complete[package]['GoFiles']:
            raise ValueError('The fixed source file is absent from the actual compiled package: ' + name)
        normalized = []
        versions = {}
        for commit in (BACKPORT, CURRENT):
            path = args.source_dir / commit / name
            raw = path.read_bytes()
            api_path = args.source_dir / commit / (name + '.api-response.json')
            api = json.loads(api_path.read_bytes())
            blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if api['path'] != name or api['sha'] != blob:
                raise ValueError('The exact official source blob differs')
            content = copy(path, commit + '/' + name + '.gz', True)
            copy(api_path, commit + '/' + name + '.api-response.json.gz', True)
            normalized.append(subprocess.check_output([str(ast_tool), str(path)]))
            versions[commit] = {'path': content, 'git_blob': blob, 'sha256': digest(path)}
        if normalized[0] != normalized[1]:
            raise ValueError('Current Tempo source changed the fixed complete Go AST: ' + name)
        sources[name] = {'compiled_package': package, 'versions': versions,
                         'normalized_ast_sha256': hashlib.sha256(normalized[0]).hexdigest()}
    log = args.regression_log.read_text(encoding='utf-8')
    for name in REGRESSIONS:
        if '=== RUN   ' + name not in log or '--- PASS: ' + name not in log:
            raise ValueError('The bounded original upstream regression did not run and pass: ' + name)
    regression = copy(args.regression_log, 'bounded-exemplar-regressions.log.gz', True)
    copy(ROOT / 'scripts/prepare_tempo_full_scope_20261115.py', 'prepare_source.py')
    proof = {'schema': 'tempo-all-severity-fixed-source-proof-20261115-v1', 'status': 'prepared',
             'actor': 'PENDING_INDEPENDENT_REVIEW', 'actor_kind': 'automated_independent_agent',
             'facts_passed': False, 'risk_acceptance': False, 'binary_redistribution_approved': False,
             'current_source_commit': CURRENT, 'stable_backport': BACKPORT,
             'actual_elf_sha256': old['scope']['elf_sha256'],
             'actual_module_version': high.VERSION, 'go_version': 'go1.27.4',
             'original_high_review_sha256': high.file_hash(ROOT / high.REVIEW_PATH),
             'medium_finding': {'target': 'tempo', 'id': 'CVE-2026-27878',
                                'package': high.PACKAGE, 'version': high.VERSION, 'severity': 'MEDIUM',
                                'fixed_version': '1.5.1-0.20260303204923-b13f74291d48'},
             'ancestry': {'path': ancestry, 'ahead_by': compare['ahead_by'], 'behind_by': 0},
             'compiled_files_context': {'path': original_context, 'raw_sha256': context['raw_sha256'],
                                        'raw_bytes': context['raw_bytes']},
             'fixed_complete_source_asts': sources, 'bounded_regressions': {'path': regression, 'tests': list(REGRESSIONS)},
             'ast_comparison': 'Official Go parser, comments preserved; token offsets become presence booleans and syntactic ParenExpr wrappers are removed. All other node kinds and fields remain exact.',
             'official_advisory': 'https://grafana.com/security/security-advisories/cve-2026-27878/',
             'evidence_sha256': hashes, 'raw_reports_modified': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(proof, indent=2) + '\n').encode())
    print(json.dumps({'status': 'prepared', 'source_equivalence': len(sources), 'regressions': len(REGRESSIONS)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--raw-context', type=Path, required=True)
    parser.add_argument('--regression-log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args())
