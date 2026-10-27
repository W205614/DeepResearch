"""Package already-frozen local outputs for human review; makes no model/network calls."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import re
import subprocess
import zipfile

FIELDS = ['sample_id', 'reviewer_code', 'review_status', 'factual_support_0_4', 'citation_fidelity_0_4',
          'request_coverage_0_4', 'uncertainty_0_4', 'readability_0_4', 'critical_error',
          'unverifiable_claims', 'evidence_spans', 'notes', 'submitted_at']
SOURCE_FIELDS = {'id', 'kind', 'title', 'url', 'text', 'access', 'locator', 'retrieved_at',
                 'published_at', 'document_hash', 'index_version', 'content_hash'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_frozen(root, name, expected, limit):
    path = (root / name).resolve()
    if path.name.startswith('.env'):
        raise ValueError('Configuration/secrets files cannot be review inputs')
    data = path.read_bytes()
    if len(data) > limit or not re.fullmatch(r'[0-9a-f]{64}', str(expected)) or digest(data) != expected:
        raise ValueError('Input size or frozen SHA-256 does not match: ' + path.name)
    return path, data


def prepare(manifest_path, output_path, mapping_path, repository=None):
    manifest_path, output_path, mapping_path = map(lambda p: Path(p).resolve(), (manifest_path, output_path, mapping_path))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    sha = manifest.get('final_sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('A concrete final Git SHA is required; placeholders cannot be packaged')
    if repository:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
        if head != sha:
            raise ValueError('Manifest final_sha does not equal the selected repository HEAD')
    if manifest.get('sensitive_information_reviewed') is not True:
        raise ValueError('An authorized operator must review sensitive content before packaging')
    if output_path.exists() or mapping_path.exists() or mapping_path.is_relative_to(output_path):
        raise ValueError('Output/mapping must be new, and the private mapping must be outside the reviewer directory')
    substitutions = manifest.get('explicit_redactions', [])
    if not isinstance(substitutions, list) or any(not isinstance(row, dict) or not isinstance(row.get('find'), str) or not row.get('find')
                                                or not isinstance(row.get('replace'), str) for row in substitutions):
        raise ValueError('Redactions must be explicit find/replace records')
    def redact(value):
        value = str(value)
        for row in substitutions:
            value = value.replace(row['find'], row['replace'])
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}', value):
            raise ValueError('Potential credential in review content; revise the explicit redaction map')
        return value
    samples = []
    seen_cases = set()
    for case in manifest.get('cases', []):
        case_id = case.get('case_id')
        if not case_id or case_id in seen_cases or not case.get('question') or not case.get('outputs'):
            raise ValueError('Every frozen case needs a unique id, question and output list')
        seen_cases.add(case_id)
        for output in case['outputs']:
            report_file, report = read_frozen(manifest_path.parent, output['report_path'], output['report_sha256'], 2_000_000)
            sources_file, sources = read_frozen(manifest_path.parent, output['sources_path'], output['sources_sha256'], 5_000_000)
            sources = json.loads(sources)
            if not isinstance(sources, list) or any(not isinstance(row, dict) for row in sources):
                raise ValueError('Sources must be a list of evidence objects')
            if any(not isinstance(value, (str, int, float, bool, type(None)))
                   for row in sources for key, value in row.items() if key in SOURCE_FIELDS):
                raise ValueError('Evidence fields must be scalar values; nested metadata cannot enter a blind pack')
            safe_sources = [{key: redact(value) if isinstance(value, str) else value
                             for key, value in row.items() if key in SOURCE_FIELDS} for row in sources]
            samples.append({'question': redact(case['question']), 'report': redact(report.decode('utf-8')),
                            'sources': safe_sources, 'private': {
                                'case_id': case_id, 'condition': output.get('condition', 'unspecified'),
                                'question_sha256': digest(case['question'].encode()),
                                'report_sha256': digest(report), 'sources_sha256': output['sources_sha256'],
                                'report_path': str(report_file), 'sources_path': str(sources_file)}})
    if not samples:
        raise ValueError('No frozen outputs are available; no empty or invented review pack is created')
    random.SystemRandom().shuffle(samples)
    files, mapping = {}, []
    for number, sample in enumerate(samples, 1):
        sample_id = f'S{number:04d}'
        report_bytes = sample['report'].encode('utf-8')
        source_bytes = json.dumps(sample['sources'], ensure_ascii=False, indent=2).encode('utf-8')
        files[f'{sample_id}/question.txt'] = sample['question'].encode('utf-8')
        files[f'{sample_id}/report.md'] = report_bytes
        files[f'{sample_id}/sources.json'] = source_bytes
        mapping.append({'sample_id': sample_id, **sample['private'], 'anonymized_report_sha256': digest(report_bytes),
                        'anonymized_sources_sha256': digest(source_bytes)})
    table = io.StringIO(newline='')
    writer = csv.DictWriter(table, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows({'sample_id': row['sample_id'], 'review_status': 'unrated'} for row in mapping)
    files['reviews.csv'] = table.getvalue().encode('utf-8-sig')
    files['review-instructions.md'] = Path(__file__).with_name('README.md').read_bytes()
    receipt = {'final_sha': sha, 'sample_count': len(samples), 'human_review': 'not_performed',
               'rules_version': 'blind-human-review-v1',
               'independent_truth_labels': 'not_established',
               'binding': 'frozen local input hashes; no claim of fresh model generation or independent factual truth',
               'files': {name: digest(data) for name, data in files.items()}}
    files['packet.json'] = json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8')
    output_path.mkdir(parents=True)
    for name, data in files.items():
        target = output_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with zipfile.ZipFile(output_path / 'reviewer-pack.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps({'final_sha': sha, 'samples': mapping}, ensure_ascii=False, indent=2), encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True, help='Private condition map, outside the reviewer directory')
    parser.add_argument('--repository', type=Path, help='When supplied, require manifest SHA to equal this repository HEAD')
    args = parser.parse_args()
    print(json.dumps(prepare(args.manifest, args.output, args.mapping, args.repository), ensure_ascii=False, indent=2))
