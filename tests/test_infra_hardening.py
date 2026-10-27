import json
import asyncio

import httpx
import pytest

from scripts.bootstrap_services import identifier
from scripts.copy_backup import checksum, copy_verified
from scripts.ops_metrics import host_metrics
from scripts.prepare_service_credentials import KEYS, prepare
from scripts.scheduled_backup import execute
from scripts.scheduled_backup import within_window
from datetime import datetime, timezone
from scripts.alert_relay_service import create_app


def test_private_credentials_are_distinct_preserved_and_not_overwritten(tmp_path):
    env = tmp_path / ".env"
    original = "POSTGRES_PASSWORD=private-existing\nJAVA_DB_PASSWORD=existing-independent-password\n"
    env.write_text(original)
    assert prepare(env) == len(KEYS) - 1
    content = env.read_text()
    assert "POSTGRES_PASSWORD=private-existing" in content
    assert "JAVA_DB_PASSWORD=existing-independent-password" in content
    assert (tmp_path / ".env.before-service-roles").read_text() == original
    values = [line.split("=", 1)[1] for line in content.splitlines() if line.split("=", 1)[0] in KEYS]
    assert len(values) == len(set(values)) == len(KEYS)
    assert prepare(env) == 0
    assert env.read_text() == content


def test_role_identifier_rejects_sql_injection():
    assert identifier("dr_python") == '"dr_python"'
    with pytest.raises(ValueError):
        identifier('dr_python; DROP DATABASE x')


def test_copy_v3_nested_configuration_and_reject_path_escape(tmp_path):
    source = tmp_path / "backup"
    (source / "binds").mkdir(parents=True)
    nested = source / "binds" / "config.txt"
    nested.write_text("synthetic config")
    manifest = {"version": 3, "files": {"binds/config.txt": checksum(nested)}}
    (source / "manifest.json").write_text(json.dumps(manifest))
    assert copy_verified(source, tmp_path / "remote") == 2
    manifest["files"]["../escaped"] = "invalid"
    (source / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        copy_verified(source, tmp_path / "bad-remote")
    assert not (tmp_path / "bad-remote").exists()


def test_monitor_fails_closed_without_probe_and_does_not_export_private_paths(tmp_path):
    path = tmp_path / "status.json"
    assert "deepresearch_backup_verified_timestamp_seconds 0.0" in host_metrics(path)
    path.write_text(json.dumps({"version": 1, "observed_at": 123, "last_verified_backup_at": 100,
                                "disks": {"workspace": {"free_bytes": 10, "total_bytes": 100},
                                          "C:/private": {"free_bytes": 0, "total_bytes": 1}}}))
    text = "\n".join(host_metrics(path))
    assert 'disk="workspace"' in text
    assert "C:/private" not in text


def test_capacity_refresh_preserves_full_verification_and_never_hashes_archives(monkeypatch, tmp_path):
    from scripts import ops_probe
    def unexpected_hash(_):
        raise AssertionError("A fast capacity sample must not rehash backup images")
    monkeypatch.setattr(ops_probe, "load_archive", unexpected_hash)
    status = ops_probe.probe(tmp_path, {"workspace": str(tmp_path)}, clock=lambda: 200,
                             previous_verification={"last_verified_backup_at": 100})
    assert status["observed_at"] == 200
    assert status["last_verified_backup_at"] == 100
    assert status["disks"]["workspace"]["total_bytes"] > 0


@pytest.mark.parametrize("previous", [{}, {"last_verified_backup_at": 300},
                                      {"last_verified_backup_at": "NaN"}])
def test_capacity_refresh_does_not_invent_a_successful_backup(tmp_path, previous):
    from scripts.ops_probe import probe
    assert probe(tmp_path, {}, clock=lambda: 200, previous_verification=previous)["last_verified_backup_at"] == 0


def test_scheduler_requires_enabled_acknowledged_maintenance_before_touching_services():
    with pytest.raises(ValueError, match="explicitly enabled"):
        execute({"enabled": False})
    with pytest.raises(ValueError, match="maintenance"):
        execute({"enabled": True})


def test_scheduler_uses_shanghai_window_and_rejects_late_catchup():
    assert within_window({}, datetime(2026, 10, 25, 19, 30, tzinfo=timezone.utc))
    assert not within_window({}, datetime(2026, 10, 26, 3, 30, tzinfo=timezone.utc))
    assert not within_window({}, datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc))


def test_scheduler_keeps_verified_local_backup_but_reports_requested_remote_failure(monkeypatch, tmp_path):
    from scripts import scheduled_backup
    monkeypatch.setattr(scheduled_backup, "within_window", lambda _: True)
    monkeypatch.setattr(scheduled_backup, "backup", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduled_backup, "load_archive", lambda _: ({}, {}))
    monkeypatch.setattr(scheduled_backup, "probe", lambda *args: {"version": 1})
    def remote_failure(*args):
        raise OSError("synthetic remote failure")
    monkeypatch.setattr(scheduled_backup, "copy_verified", remote_failure)
    with pytest.raises(OSError):
        execute({"enabled": True, "maintenance_window_acknowledged": True,
                 "backup_root": str(tmp_path / "backup"), "remote_target": str(tmp_path / "remote"),
                 "remote_host_attested": True, "remote_access_and_encryption_attested": True,
                 "host_status_file": str(tmp_path / "host.json")})
    report = json.loads((tmp_path / "backup/scheduler-status.json").read_text())
    assert report["local_backup_verified"] is True
    assert report["success"] is False
    assert report["remote_status"] == "copy_failed"


@pytest.mark.asyncio
async def test_alert_delivery_serializes_and_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_RELAY_STATE_FILE", str(tmp_path / "relay.sqlite3"))
    monkeypatch.setenv("ALERT_RELAY_TOKEN", "synthetic-relay-token")
    monkeypatch.delenv("ALERT_RELAY_TOKEN_FILE", raising=False)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "http://controlled-recorder/webhook")
    delivered = []

    async def sender(url, body):
        delivered.append(body)
        await asyncio.sleep(0)

    payload = {"alerts": [{"fingerprint": "fixture", "status": "firing", "labels": {"alertname": "Synthetic"}}]}
    headers = {"Authorization": "Bearer synthetic-relay-token"}
    app = create_app()
    app.state.sender = sender
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        responses = await asyncio.gather(*(client.post("/internal/alerts", json=payload, headers=headers) for _ in range(3)))
        assert sum(response.json()["delivered"] for response in responses) == 1
    app = create_app()
    app.state.sender = sender
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
        assert (await client.post("/internal/alerts", json=payload, headers=headers)).json()["reason"] == "deduplicated"
        payload["alerts"][0]["status"] = "resolved"
        assert (await client.post("/internal/alerts", json=payload, headers=headers)).json()["delivered"]
    assert len(delivered) == 2
