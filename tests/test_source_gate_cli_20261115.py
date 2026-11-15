"""Replay real complete source evidence through the production migration path."""
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path

from scripts import source_dependency_migration_20261115 as migration
from scripts.retained_source_review_20261112 import snapshot

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/source-gate-20261115'


def test_current_manifest_coverage_matches_both_preserved_cli_verdicts(monkeypatch):
    metadata = json.loads((FIXTURE / 'metadata.json').read_bytes())
    original_bytes = {}
    for name, identity in metadata['files'].items():
        packed = (FIXTURE / (name + '.gz')).read_bytes()
        assert hashlib.sha256(packed).hexdigest() == identity['gzip_sha256']
        original_bytes[name] = gzip.decompress(packed)
        assert hashlib.sha256(original_bytes[name]).hexdigest() == identity['sha256']
    scan, receipt, original, candidate = [json.loads(original_bytes[name]) for name in
                                         ('raw.json', 'completion.json', 'verdict.json', 'candidate-verdict.json')]
    observed = datetime.fromisoformat(receipt['finished_at']) + timedelta(seconds=1)

    class RecordedClock:
        @staticmethod
        def now(tz=None):
            return observed.astimezone(tz) if tz else observed.replace(tzinfo=None)

    monkeypatch.setattr(migration, 'datetime', RecordedClock)
    source = metadata['source_commit']
    expected = migration.prior.old.committed_manifest_targets(ROOT, source)
    assert len(expected) == metadata['scan_targets'] == 80
    args = dict(raw_sha256=metadata['files']['raw.json']['sha256'], source_commit=source,
                root=ROOT, now=observed, tracked_clean=True, expected_targets=expected)
    assert migration.prior.old.evaluate(scan, receipt, **args) == original
    actual_candidate = migration.prior.candidate.evaluate(scan, receipt, original, **args)
    assert actual_candidate == {k:v for k,v in candidate.items() if k != 'preserved_original_verdict_file_sha256'}
    historical_root = snapshot(ROOT)
    historical = migration.build.load_profile(historical_root)
    result = migration.collect(scan, receipt, original, candidate,
                               raw_sha256=args['raw_sha256'],
                               original_sha256=metadata['files']['verdict.json']['sha256'],
                               source_commit=source, tracked_clean=True,
                               historical=historical, historical_root=historical_root, root=ROOT)
    assert result['passed'] is True and result['unresolved_count'] == 0
    assert result['verified_all_severity_findings'] == metadata['raw_findings'] == 103
