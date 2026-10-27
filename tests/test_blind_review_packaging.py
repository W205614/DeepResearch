import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


MODULE = Path(__file__).resolve().parents[1] / 'docs/evidence/optimization-20261026/blind-review/prepare_blind_review.py'
spec = importlib.util.spec_from_file_location('blind_review_packager', MODULE)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def frozen_manifest(tmp_path):
    report = b'# Fixture answer [W-1]'
    sources = json.dumps([{'id': 'W-1', 'kind': 'web', 'text': 'Fixture evidence',
                           'api_key': 'MUST-NOT-ENTER-PACKET', 'provider_model': 'HIDDEN-CONDITION'}]).encode()
    (tmp_path / 'report.md').write_bytes(report)
    (tmp_path / 'sources.json').write_bytes(sources)
    manifest = {'final_sha': '1' * 40, 'sensitive_information_reviewed': True,
                'cases': [{'case_id': 'private-case-id', 'question': 'Fixture question', 'outputs': [{
                    'condition': 'HIDDEN-CONDITION', 'report_path': 'report.md', 'sources_path': 'sources.json',
                    'report_sha256': hashlib.sha256(report).hexdigest(),
                    'sources_sha256': hashlib.sha256(sources).hexdigest()}]}]}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    return path, manifest


def test_pack_contains_no_condition_or_secret_metadata_and_scores_remain_blank(tmp_path):
    manifest, _ = frozen_manifest(tmp_path)
    output, mapping = tmp_path / 'reviewer', tmp_path / 'private.json'
    receipt = packager.prepare(manifest, output, mapping)
    assert receipt['human_review'] == 'not_performed'
    with zipfile.ZipFile(output / 'reviewer-pack.zip') as archive:
        packed = b' '.join(archive.read(name) for name in archive.namelist())
        assert b'HIDDEN-CONDITION' not in packed and b'MUST-NOT-ENTER-PACKET' not in packed
        assert b'private-case-id' not in packed
    rows = list(csv.DictReader((output / 'reviews.csv').open(encoding='utf-8-sig', newline='')))
    assert len(rows) == 1 and rows[0]['factual_support_0_4'] == '' and rows[0]['review_status'] == 'unrated'
    assert json.loads(mapping.read_text())['samples'][0]['condition'] == 'HIDDEN-CONDITION'
    with pytest.raises(ValueError, match='must be new'):
        packager.prepare(manifest, output, tmp_path / 'other.json')


@pytest.mark.parametrize('change', ['sha_placeholder', 'missing_privacy_review', 'report_hash_mismatch', 'mapping_inside_packet'])
def test_invalid_or_unreviewed_inputs_fail_before_output_creation(tmp_path, change):
    path, manifest = frozen_manifest(tmp_path)
    output, mapping = tmp_path / 'reviewer', tmp_path / 'private.json'
    if change == 'sha_placeholder':
        manifest['final_sha'] = 'PENDING'
    elif change == 'missing_privacy_review':
        manifest['sensitive_information_reviewed'] = False
    elif change == 'report_hash_mismatch':
        (tmp_path / 'report.md').write_text('changed after freeze')
    else:
        mapping = output / 'private.json'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        packager.prepare(path, output, mapping)
    assert not output.exists()
