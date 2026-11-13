"""Original source findings remain exact around the two reviewed CSS-path rows."""
import copy
import gzip
import json
from pathlib import Path

import pytest

from scripts import check_grafana_source_scan_20261112 as gate


def original_source_findings():
    path = Path(__file__).resolve().parents[1] / 'deployment/security/grafana-source-20261112-evidence/source-ci6d-full73.json.gz'
    return gate.prior.source_facts(json.loads(gzip.decompress(path.read_bytes())))[0]


def test_complete_original73_and_current_application_only_css_scope():
    rows = gate.python_source_scope(original_source_findings())
    assert [(row['id'], row['severity']) for row in rows] == [
        ('CVE-2026-126190', 'HIGH'), ('CVE-2026-126192', 'MEDIUM')]
    assert all(row['target'] == 'uv.lock' and row['package'] == 'soupsieve' and row['version'] == '2.9.2' for row in rows)


@pytest.mark.parametrize('change', ['missing', 'same_count_unknown', 'severity'])
def test_current_source_scope_never_accepts_new_missing_or_changed_records(change):
    observed = copy.deepcopy(original_source_findings())
    if change == 'missing':
        observed['findings'].pop()
    elif change == 'same_count_unknown':
        observed['findings'][0]['id'] = 'CVE-UNREVIEWED'
    else:
        observed['findings'][0]['severity'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='Complete73'):
        gate.python_source_scope(observed)
