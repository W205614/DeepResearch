"""Keep calendar amendments distinct from changed active runtime selection."""
from datetime import datetime, timezone

import pytest

from scripts import check_fs_source_applicability as fs
from scripts import check_grafana_active_source_retirement as retirement
from scripts import check_grafana_candidate_exact28 as candidate
from scripts import check_grafana_candidate_source_exact2 as source
from scripts import check_tempo_app_exact1 as tempo
from scripts import grafana_image_disposition as grafana
from scripts import image_disposition as minio
from scripts import security_review_policy as policy


FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
LOADERS = {
    'minio': lambda: minio.load_binding(now=FUTURE),
    'legacy-grafana': lambda: grafana.load_binding(now=FUTURE),
    'current-grafana': lambda: candidate.load_request(now=FUTURE),
    'legacy-source2': lambda: fs.load_scope(fs.ROOT, fs.POLICY, FUTURE),
    'current-source2': lambda: source.load_review(source.ROOT, FUTURE),
    # The old selector controls changed when maintenance became active. Its exact
    # historical record still has the human calendar amendment; the old full
    # consumer must reject those changed controls, as checked separately below.
    'historical-active-retirement-policy': lambda: policy.validate_review_policy(
        retirement.ROOT, retirement.REVIEW_PATH, now=FUTURE),
    'tempo': lambda: tempo.load_review(now=FUTURE),
}


@pytest.mark.parametrize('consumer', LOADERS)
def test_unchanged_real_subject_does_not_expire_by_date(consumer):
    assert LOADERS[consumer]()


@pytest.mark.parametrize('consumer', LOADERS)
def test_real_consumer_requires_the_exact_human_amendment(consumer, monkeypatch):
    monkeypatch.setattr(policy, 'POLICY_SHA256', 'f' * 64)
    with pytest.raises(ValueError, match='review policy bytes changed'):
        LOADERS[consumer]()


def test_legacy_selection_cannot_inherit_the_new_maintenance_controls():
    with pytest.raises(ValueError, match='Conditional source or control input changed'):
        retirement.load_review(retirement.ROOT, FUTURE)


def test_historical_maintenance_selection_survives_date_only_after_complete_real_binding(historical_grafana_maintenance_root):
    review = retirement.load_review(historical_grafana_maintenance_root, FUTURE, profile='maintenance')
    assert review['validity'] == 'change-driven'
    assert review['facts_passed'] is True
    assert review['subject']['archive_sha256'] == '408744f9d8d357a9b7dfd97ac802efbbdee662cb1177b80d8020bdc24c019e25'
    assert review['publication_sha256'] and review['transport_sha256']


def test_date_amendment_cannot_refresh_changed_live_maintenance_controls():
    # Rejecting an earlier selector check after profile activation is valid too.
    with pytest.raises(ValueError):
        retirement.load_review(retirement.ROOT, FUTURE, profile='maintenance')
