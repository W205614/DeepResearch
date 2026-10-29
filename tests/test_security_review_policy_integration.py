"""Exercise all seven real consumers beyond the superseded calendar deadline."""
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
    'active-retirement': lambda: retirement.load_review(retirement.ROOT, FUTURE),
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
