import json
import sys

import pytest

from scripts.cloud_backup_metrics import main, render


def record(success=100):
    return {"version": 1, "observed_at": 200,
            "resources": [{"resource": "postgres-primary", "last_success_at": success, "rpo_seconds": 86400}]}


def test_provider_observation_is_retained_and_no_success_is_invented():
    metrics = render(record(0), clock=lambda: 1000)
    assert 'deepresearch_cloud_backup_last_success_timestamp_seconds{resource="postgres-primary"} 0\n' in metrics
    assert 'deepresearch_cloud_backup_observed_timestamp_seconds{resource="postgres-primary"} 200\n' in metrics
    assert 'deepresearch_cloud_backup_required{resource="postgres-primary"} 1\n' in metrics


@pytest.mark.parametrize("update", [
    {"last_success_at": 201}, {"last_success_at": float("nan")},
    {"resource": "private/path"}, {"rpo_seconds": 0}, {"rpo_seconds": True},
])
def test_invalid_provider_status_fails_instead_of_exporting_success(update):
    data = record()
    data["resources"][0].update(update)
    with pytest.raises(ValueError):
        render(data, clock=lambda: 1000)


def test_real_file_output_uses_prometheus_newlines_and_invalid_input_preserves_previous_metrics(tmp_path, monkeypatch):
    source = tmp_path / "provider.json"
    output = tmp_path / "backup.prom"
    source.write_text(json.dumps(record()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["cloud_backup_metrics.py", "--input", str(source), "--output", str(output)])
    main()
    original = output.read_bytes()
    assert b"\r" not in original
    source.write_text(json.dumps(record(201)), encoding="utf-8")
    with pytest.raises(ValueError):
        main()
    assert output.read_bytes() == original
