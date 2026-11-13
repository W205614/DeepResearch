from copy import deepcopy

import pytest

from scripts.check_keycloak_zlib_20261113 import CVE, VERSION, full_counts


def report():
    return {'descriptor': {'name': 'grype', 'version': '0.121.0', 'db': {'status': {'valid': True}}},
            'source': {'target': {'imageID': 'sha256:actual'}}, 'matches': [
                {'vulnerability': {'id': CVE, 'severity': 'High', 'namespace': 'redhat:distro:redhat:9'},
                 'artifact': {'name': 'zlib', 'version': VERSION, 'type': 'rpm'}}]}


def test_only_exact_source_fixed_tuple_and_unmodified_lower_counts():
    raw = report()
    raw['matches'].append({'vulnerability': {'id': 'retained-low', 'severity': 'Low'}, 'artifact': {}})
    before = deepcopy(raw)
    assert full_counts(raw, {'source': {'metadata': {'imageID': 'sha256:actual'}}}) == ({'High': 1, 'Low': 1}, 1)
    assert raw == before


@pytest.mark.parametrize(('part', 'key', 'value'), [
    ('vulnerability', 'id', 'CVE-unreviewed'), ('vulnerability', 'severity', 'Critical'),
    ('vulnerability', 'namespace', 'other'), ('artifact', 'name', 'other'),
    ('artifact', 'version', '1.2.11-40.el9'), ('artifact', 'type', 'binary')])
def test_other_blocking_tuple_is_rejected(part, key, value):
    raw = report()
    raw['matches'][0][part][key] = value
    with pytest.raises(ValueError, match='Unreviewed'):
        full_counts(raw, {'source': {'metadata': {'imageID': 'sha256:actual'}}})


def test_wrong_image_and_duplicate_or_ignored_findings_are_rejected():
    sbom = {'source': {'metadata': {'imageID': 'sha256:actual'}}}
    raw = report()
    with pytest.raises(ValueError, match='image'):
        full_counts(raw, {'source': {'metadata': {'imageID': 'sha256:other'}}})
    raw['matches'] *= 2
    with pytest.raises(ValueError, match='Expanded'):
        full_counts(raw, sbom)
    raw = report()
    raw['ignoredMatches'] = [raw['matches'][0]]
    with pytest.raises(ValueError, match='Invalid'):
        full_counts(raw, sbom)
