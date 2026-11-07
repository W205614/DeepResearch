"""Four exact candidate-manifest facts; never approve a runtime artifact."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json

REVIEW_PATH = 'deployment/security/grafana-source-candidate4-review-20261107.json'
REVIEW_SHA256 = '48aa2f815ff1bf0213107fda270e7a72fe9ba826929eb25a0ce6f6b3f85c9ce1'
PREFIX = 'deployment/vendors/grafana-20261107/'
TEMPO = 'github.com/grafana/tempo'
IDS = frozenset({'CVE-2026-21728', 'CVE-2026-28377'})
SOURCES = {PREFIX + 'main/go.mod': ('main', 'v1.5.1-0.20260427112133-525d1bab07e0'),
           PREFIX + 'plugins/tempo/go.mod': ('tempo', 'v1.5.1-0.20260910130453-bcfe9f230c1d')}
FIXES = {'CVE-2026-21728': ('650eb1985a0776789c8564122990f588a742356f', 'modules/frontend/config.go'),
         'CVE-2026-28377': ('bb8ca663db34a0980c9758b40d918fda3b4dbec3', 'tempodb/backend/s3/config.go')}
AFFECTED = (TEMPO + '/modules/frontend', TEMPO + '/tempodb/backend/s3')
UNKNOWN_TARGETS = (PREFIX + 'main/go.mod', *(PREFIX + 'plugins/' + name + '/go.mod'
                    for name in ('elasticsearch', 'loki', 'mssql', 'stackdriver')))
OLD_ERROR = 'Full raw source findings must be exactly the original five plus the independently reviewed nine'


def tuples(rows):
    return Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity')) for row in rows)


def graph_facts(g, raw, native):
    """Use the existing strict decoder/effective-identity rules over the entire closure."""
    decoder = json.JSONDecoder(object_pairs_hook=g.source.old.unique_object,
                               parse_constant=g.source.old.invalid_constant)
    text, offset, packages, modules, imports = raw.decode('utf-8'), 0, {}, {}, set()
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        row, offset = decoder.raw_decode(text, offset)
        g.require(type(row) is dict and not row.get('Error') and not row.get('Incomplete')
                  and not row.get('DepsErrors'), 'Candidate production graph is incomplete')
        name, values = row.get('ImportPath'), row.get('Imports', [])
        g.require(type(name) is str and name and name not in packages
                  and type(values) is list and all(type(v) is str and v for v in values),
                  'Candidate package identity/imports are malformed or repeated')
        packages[name] = row
        imports.update(values)
        if 'Module' in row:
            module = row['Module']
            g.require(type(module) is dict and type(module.get('Path')) is str and module['Path'],
                      'Candidate graph module identity is missing')
            identity = {key: module.get(key) for key in ('Path', 'Version', 'Sum', 'Main')}
            if module.get('Replace'):
                identity['Replace'] = module['Replace']
            g.require(module['Path'] not in modules or modules[module['Path']] == identity,
                      'Candidate graph module metadata is ambiguous')
            modules[module['Path']] = identity
    g.require(packages and imports <= packages.keys(), 'Candidate production dependency closure is incomplete')
    deps = [native['Main'], *native['Deps']]
    actual = {dep['Path']: dep for dep in deps}
    g.require(len(actual) == len(deps) and set(actual) == set(modules),
              'Full candidate graph/native module coverage differs')
    for path, module in modules.items():
        if module.get('Main'):
            g.require(module.get('Version') is None and module.get('Sum') is None
                      and not module.get('Replace') and not actual[path].get('Replace')
                      and not actual[path].get('Sum'), 'Candidate workspace module identity differs')
        else:
            g.require(g.source.effective(module) == g.source.effective(actual[path]),
                      'Candidate effective native dependency path/version/sum differs')
    return packages, modules, actual


def verify_builds(g, review, root):
    result = {}
    for name, spec in review['builds'].items():
        source_path = spec['source']
        g.require(SOURCES[source_path][0] == name
                  and g.approved.file_hash(root / source_path) == spec['source_sha256'],
                  'Candidate manifest bytes differ from the reviewed actual build')
        native = g.source.old.json_loads((root / spec['native']).read_bytes())
        receipt = g.source.old.json_loads((root / spec['graph_receipt']).read_bytes())
        graph_file = root / spec['graph']
        g.require(g.approved.file_hash(graph_file) == receipt['compressed_sha256'], 'Candidate graph archive changed')
        with gzip.open(graph_file, 'rb') as stream:
            raw = stream.read(g.source.old.MAX_REPORT_BYTES + 1)
        g.require(len(raw) == receipt['raw_bytes'] <= g.source.old.MAX_REPORT_BYTES
                  and hashlib.sha256(raw).hexdigest() == receipt['raw_sha256'], 'Candidate full raw graph changed')
        packages, modules, actual = graph_facts(g, raw, native)
        g.require(len(packages) == receipt['package_count'] == spec['packages']
                  and len(modules) == receipt['module_count'] == spec['modules']
                  and receipt['completed'] is True and receipt['exit_code'] == 0
                  and receipt['raw_graph_modified_or_filtered'] is False, 'Candidate graph coverage differs')
        settings = {row['Key']: row['Value'] for row in native['Settings']}
        g.require(len(settings) == len(native['Settings']) and native['GoVersion'] == 'go1.27.3'
                  and native['Path'] == spec['entry_package']
                  and settings['CGO_ENABLED'] == '0' and settings['GOOS'] == 'linux'
                  and settings['GOARCH'] == 'amd64' and settings['GOAMD64'] == 'v1'
                  and settings['vcs.revision'] == spec['vcs_revision'] and settings['vcs.modified'] == 'true'
                  and settings.get('-tags', '') == spec['tags'] and settings['-trimpath'] == 'true',
                  'Candidate actual compiler/source profile differs')
        environment = receipt['go_environment']
        g.require(receipt['compiler'] == 'go version go1.27.3 linux/amd64'
                  and environment['CGO_ENABLED'] == '0' and environment['GOOS'] == 'linux'
                  and environment['GOARCH'] == 'amd64' and environment['GOAMD64'] == 'v1'
                  and receipt['build_tags'] == (spec['tags'] or None), 'Candidate graph compiler profile differs')
        g.require(receipt['actual_lock_sha256']['go.mod'] == spec['source_sha256'],
                  'Candidate actual compiled manifest differs')
        names = set(packages)
        affected = sorted(p for p in names if any(p == prefix or p.startswith(prefix + '/') for prefix in AFFECTED))
        g.require(not affected, 'Affected Tempo server package is compiled into the candidate')
        tempo = sorted(p for p in names if p == TEMPO or p.startswith(TEMPO + '/'))
        if name == 'main':
            g.require(not tempo and TEMPO not in actual
                      and all(dep.get('Replace', {}).get('Path') != TEMPO for dep in actual.values()),
                      'Tempo module or imports are compiled into candidate main')
        else:
            g.require(tempo == [TEMPO + suffix for suffix in ('/pkg/tempopb', '/pkg/tempopb/common/v1',
                      '/pkg/tempopb/resource/v1', '/pkg/tempopb/trace/v1')]
                      and actual[TEMPO] == spec['actual_tempo_dependency'], 'Candidate Tempo dependency/package scope changed')
        with gzip.open(root / spec['functions'], 'rb') as stream:
            functions = g.source.old.json_loads(stream.read(g.source.old.MAX_REPORT_BYTES + 1))
        g.require(functions['elf_sha256'] == spec['elf_sha256']
                  and len(functions['linked_functions']) == functions['function_count'] == spec['functions_count']
                  and not any(any(prefix in function for prefix in AFFECTED) for function in functions['linked_functions']),
                  'Candidate actual ELF/function evidence differs')
        result[name] = {'source': source_path, 'source_sha256': spec['source_sha256'],
                        'elf_sha256': spec['elf_sha256'], 'packages': len(packages), 'modules': len(modules),
                        'tempo_packages': tempo, 'affected_server_packages': affected,
                        'complete_effective_native_dependencies_equal': True}
    g.require(set(result) == {'main', 'tempo'}, 'Both exact candidate build proofs are required')
    return result


def load_review(g, root):
    path = g.approved.repository_file(root, REVIEW_PATH)
    g.require(g.approved.file_hash(path) == REVIEW_SHA256, 'Candidate-four independent review bytes changed')
    review = g.source.old.json_loads(path.read_bytes())
    g.require(review['schema'] == 'candidate-four-source-technical-applicability-v1'
              and review['actor_kind'] == 'automated_independent_agent' and review['facts_passed'] is True
              and review['artifact_approved'] is False and review['human_risk_acceptance'] is False
              and review['authorization_inherited'] is False, 'No exact candidate-four technical review')
    g.require(tuples(review['rows']) == Counter((id, path, TEMPO, version, 'HIGH')
              for path, (_, version) in SOURCES.items() for id in IDS) and len(review['rows']) == 4,
              'Only the four exact candidate source tuples are reviewed')
    for name, sha in review['evidence_files'].items():
        g.require(g.approved.file_hash(g.approved.repository_file(root, name)) == sha,
                  'Candidate-four source/native/graph/advisory evidence changed')
    binding = g.source.old.json_loads((root / review['image_binding']).read_bytes())
    g.require(binding['validation_only'] is True and binding['artifact_approved'] is False
              and binding['actual_image_id'] == review['validation_image_id'], 'Candidate validation image identity differs')
    for name, spec in review['builds'].items():
        g.require(binding['actual_ELFs'][name]['sha256'] == spec['elf_sha256']
                  and binding['actual_ELFs'][name]['target'] == spec['target'], 'Candidate actual image/ELF binding differs')
    for id, (fix, affected_file) in FIXES.items():
        advisory = g.source.old.json_loads((root / review['fixed_commit_evidence'][id]).read_bytes())
        g.require(advisory['sha'] == fix and affected_file in {f['filename'] for f in advisory['files']},
                  'Official Tempo fixed-commit/package scope differs')
        ancestry = g.source.old.json_loads((root / review['ancestry_evidence'][id]).read_bytes())
        g.require(ancestry['status'] == 'ahead' and ancestry['behind_by'] == 0
                  and ancestry['base_commit']['sha'] == ancestry['merge_base_commit']['sha'] == fix
                  and ancestry['html_url'].endswith(fix + '...bcfe9f230c1d'), 'Actual Tempo dependency lacks its fixed commits')
    return review, verify_builds(g, review, root)


def apply_candidate4(g, scan, prior, html_review, review, builds, previous):
    result = {'passed': False, 'raw_report_modified': False, 'artifact_approved': False,
              'human_risk_acceptance': False, 'global_security_approved': False}
    findings, counts, secrets = g.source.old.summarize(scan)
    g.require(not any(secrets.values()), 'Every secret blocks candidate source applicability')
    legacy = Counter([g.SCOPE] + [(id, path, g.source.MODULE, g.source.VERSION, 'HIGH')
        for path in (g.OLD_SOURCE, g.source.SOURCE) for id in sorted(g.source.IDS)])
    html, extra = tuples(html_review['rows']), tuples(review['rows'])
    blocking = tuples([r for r in findings if r['severity'] in g.source.old.BASELINE_GATE_SEVERITIES])
    observed_extra = tuples([r for r in findings if r['target'] in SOURCES and r['id'] in IDS])
    g.require(blocking == legacy + html + extra and observed_extra == extra
              and sum(blocking.values()) == 18, 'Full raw source must contain exactly legacy5 plus HTML9 plus candidate4')
    companion = tuples([r for r in findings if r['id'] == 'CVE-2026-97045'])
    g.require(companion == Counter(('CVE-2026-97045', target, g.HTML_MODULE, 'v0.60.0', 'MEDIUM')
              for target in g.HTML_SOURCES), 'Original raw HTML MEDIUM companion tuples changed')
    unknown = tuples([r for r in findings if r['id'] == 'GO-2026-5932' and r['target'] in UNKNOWN_TARGETS])
    g.require(unknown == Counter(('GO-2026-5932', target, 'golang.org/x/crypto', 'v0.58.0', 'UNKNOWN')
              for target in UNKNOWN_TARGETS), 'Five visible nonblocking candidate UNKNOWN tuples changed')
    targets = sorted([r['Target'], r.get('Class', ''), r.get('Type', '')] for r in scan['Results'])
    g.require(targets == review['fresh_scan_targets'] and len(targets) == 62,
              'Full current sixty-two target scan coverage differs')
    remaining = prior['unresolved']
    g.require(len(remaining) == 14 and all(r['kind'] == 'vulnerability' for r in remaining)
              and tuples(remaining) == Counter({g.SCOPE: 1}) + html + extra,
              'Only exact legacy jose, HTML9 and candidate4 may receive additional dispositions')
    try:
        g.apply_retirement(prior, scan, review_sha256=g.MAINTENANCE_REVIEW_SHA256)
    except ValueError as error:
        legacy_failure = {'passed': False, 'error': str(error), 'full_raw_blocking_count': 18}
    else:
        raise ValueError('Original five-row route unexpectedly passed the full eighteen-row report')
    retired = []
    mapping = {item['source']: item for item in html_review['native_source_facts']}
    for row in remaining:
        if row['id'] == g.SCOPE[0]:
            retired.append({**row, 'technical_disposition': 'NOT_SELECTED_BY_CURRENT_AUTHORIZED_PRODUCTION_BUILD'})
        elif row['id'] == 'CVE-2026-78661':
            fact = mapping[row['target']]
            retired.append({**row, 'technical_disposition': 'NOT_SELECTED_BY_EXACT_AUTHORIZED_FIXED_MODULE_BUILD',
                'selected_target': fact['selected_target'], 'selected_elf_sha256': fact['selected_elf_sha256'],
                'actual_native_dependency': fact['actual_native_dependency']})
        else:
            fact = builds[SOURCES[row['target']][0]]
            retired.append({**row, 'technical_disposition': 'NOT_APPLICABLE_TO_EXACT_CANDIDATE_COMPILED_PACKAGE_SCOPE',
                'actual_candidate_elf_sha256': fact['elf_sha256'], 'candidate_source_sha256': fact['source_sha256']})
    result.update(passed=True, mode='exact_current_source18_technical_appendix',
        preserved_candidate_gate=deepcopy(prior),
        preserved_legacy5_failed_verdict=legacy_failure, preserved_source14_failed_verdict=deepcopy(previous),
        raw_blocking_count=18, legacy_blocking_count=5, html_blocking_count=9, candidate_blocking_count=4,
        full_result_target_count=62, raw_counts=counts, visible_candidate_unknown_count=5,
        candidate_unknown_dispositions_granted=0, technical_not_applicable_count=18,
        technical_not_applicable=deepcopy(prior['technical_not_applicable']) + retired,
        unresolved_count=0, unresolved=[], candidate_compiled_facts=builds,
        candidate_source_review_sha256=REVIEW_SHA256, existing_runtime_selection_verified=True,
        candidate_runtime_authorized=False, original_and_appendix_failures_preserved=True)
    return result


def evaluate(g, scan, receipt, original, candidate, inventory, *, previous, root, **kwargs):
    """Evaluate the full fresh report independently, retaining both older failures."""
    result = {'passed': False, 'mode': 'candidate_four_source_failed_closed',
              'preserved_source14_failed_verdict': deepcopy(previous), 'raw_report_modified': False,
              'artifact_approved': False, 'human_risk_acceptance': False, 'global_security_approved': False}
    try:
        g.require(previous.get('passed') is False and previous.get('error') == OLD_ERROR,
                  'The genuine original fourteen-row route must fail independently first')
        now = kwargs.get('now') or g.datetime.now(g.timezone.utc)
        prior = g.source.evaluate(scan, receipt, original, raw_sha256=kwargs['raw_sha256'],
            source_commit=kwargs['source_commit'], root=root, now=now,
            tracked_clean=kwargs.get('tracked_clean', True), expected_targets=kwargs.get('expected_targets'))
        saved = {key: value for key, value in candidate.items() if key != 'preserved_original_verdict_file_sha256'}
        g.require(saved == prior and candidate['preserved_original_verdict_file_sha256'] == kwargs['original_file_sha256'],
                  'Preserved original/candidate source verdict differs')
        legacy_review = g.load_review(root, now, profile='maintenance')
        g.verify_selectors(root, inventory, legacy_review, kwargs['source_commit'], profile='maintenance')
        html_review = g.load_html_review(root, legacy_review)
        review, builds = load_review(g, root)
        result = apply_candidate4(g, scan, prior, html_review, review, builds, previous)
        result['preserved_original_verdict'] = deepcopy(original)
        result['preserved_candidate_source2_verdict'] = deepcopy(candidate)
    except (ValueError, KeyError, TypeError, OSError, EOFError, UnicodeError, RuntimeError, g.subprocess.SubprocessError) as error:
        result['error_type'] = type(error).__name__
        result['error'] = str(error) if isinstance(error, ValueError) else 'Candidate source applicability evidence is unavailable'
    return result
