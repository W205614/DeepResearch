"""Small mocked archive/import tests; no scanner, Docker, network, or real DB."""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess

import pytest

from scripts import check_native_jvm as gate


@pytest.fixture
def controlled_import(tmp_path, monkeypatch):
    archive = b"synthetic fixed archive"
    archive_sha = hashlib.sha256(archive).hexdigest()
    built = datetime(2026, 10, 26, 6, 38, 44, tzinfo=timezone.utc)
    spec = {
        "url": "https://example.invalid/fixed.tar.zst?checksum=sha256%3A" + archive_sha,
        "archive_sha256": archive_sha, "schema": "v6.1.10",
        "built": "2026-10-26T06:38:44Z", "max_snapshot_age_days": 7,
        "db_sha256": "1" * 64, "db_bytes": 3344613376,
    }
    tool = tmp_path / "pinned-grype"
    tool.write_bytes(b"mock verified executable")
    cache, output = tmp_path / "cache", tmp_path / "output"
    output.mkdir()
    state = {"payload": archive, "headers": {"Content-Length": str(len(archive))},
             "imports": [], "statuses": [], "requests": [], "metadata_updates": {}, "status_updates": {}, "exit": 0}

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(state["payload"])
            self.headers = state["headers"]

        def geturl(self):
            return state.get("redirect", "https://example.invalid/fixed.tar.zst")

    def fetch(request, timeout):
        assert isinstance(request, gate.Request)
        assert "checksum=" not in request.full_url and timeout == 60
        state["requests"].append(request)
        return Response()

    def process(argv, *, env, **kwargs):
        # The only subprocess is a mocked pinned Grype DB command, never a real executable.
        assert argv[0] == str(tool) and argv[1] == "--config"
        config = json.loads(Path(argv[2]).read_text())
        assert config["db"]["validate-by-hash-on-start"] is True
        assert config["db"]["validate-age"] is True and config["db"]["auto-update"] is False
        assert env["GRYPE_DB_VALIDATE_BY_HASH_ON_START"] == "true"
        assert env["GRYPE_DB_VALIDATE_AGE"] == "true" and env["GRYPE_DB_AUTO_UPDATE"] == "false"
        fresh = Path(env["GRYPE_DB_CACHE_DIR"])
        db = fresh / "6/vulnerability.db"
        if argv[4] == "import":
            state["imports"].append(argv)
            assert Path(argv[5]).read_bytes() == archive
            if state["exit"]:
                return subprocess.CompletedProcess(argv, state["exit"], b"import output", b"import failed")
            db.parent.mkdir()
            db.write_bytes(b"controlled derived database")
            metadata = {"source": "manual import", "client_version": "v6.1.10", "digest": "xxh64:" + "a" * 16}
            metadata.update(state["metadata_updates"])
            (db.parent / "import.json").write_text(state.get("raw_metadata", json.dumps(metadata)))
            if state.get("change_archive"):
                Path(argv[5]).write_bytes(b"changed archive")
            return subprocess.CompletedProcess(argv, 0, b"imported", b"")
        assert argv[4:] == ["status", "-o", "json"]
        state["statuses"].append(argv)
        metadata = json.loads((db.parent / "import.json").read_text())
        status = {"valid": metadata["digest"] == "xxh64:" + "a" * 16,
                  "schemaVersion": "v6.1.10", "built": spec["built"],
                  "path": str(db), "from": "manual import"}
        status.update(state["status_updates"])
        if state.get("change_during_status"):
            db.write_bytes(b"changed during status")
        return subprocess.CompletedProcess(argv, 0, json.dumps(status).encode(), b"")

    monkeypatch.setattr(gate, "urlopen", fetch)
    monkeypatch.setattr(gate.subprocess, "run", process)
    evidence = {}

    def prepare(now=None):
        return gate.prepare_database(tool, spec, cache, output,
                                     {"GRYPE_DB_VALIDATE_BY_HASH_ON_START": "false", "GRYPE_DB_CACHE_DIR": "/untrusted"},
                                     evidence, now=now or built + timedelta(days=1))

    return prepare, state, evidence, cache, output, spec


def test_archive_transport_identifies_pinned_grype_and_requests_identity_encoding(controlled_import):
    prepare, state, evidence, _, _, _ = controlled_import
    prepare()
    assert len(state["requests"]) == 1
    request = state["requests"][0]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers == {"user-agent": "grype/0.121.0", "accept-encoding": "identity"}
    assert request.full_url == "https://example.invalid/fixed.tar.zst"
    assert evidence["archive"]["request_headers"] == {
        "User-Agent": "grype/0.121.0", "Accept-Encoding": "identity"}


def test_verified_archive_fresh_import_ignores_historical_hydrated_reference_and_old_cache(controlled_import):
    prepare, state, evidence, cache, _, _ = controlled_import
    old = cache / "6/vulnerability.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"unrelated prior cache")
    prepared = prepare()
    assert prepared.path != old and old.read_bytes() == b"unrelated prior cache"
    assert len(state["imports"]) == len(state["statuses"]) == 1
    assert evidence["archive"]["verified"] is True
    assert evidence["preexisting_database_reused"] is False
    assert evidence["derived_database"]["sha256"] != evidence["historical_hydrated_reference"]["sha256"]
    prepared.assert_unchanged("after-native-scan")
    prepared.assert_unchanged("after-full-scan")
    assert [r["stage"] for r in evidence["derived_integrity_checks"]] == [
        "after-status", "after-native-scan", "after-full-scan"]


@pytest.mark.parametrize("case", ["wrong-sha", "truncated", "oversize-header", "oversize-body", "http-redirect"])
def test_unverified_or_incomplete_archive_never_reaches_import(controlled_import, monkeypatch, case):
    prepare, state, evidence, _, _, _ = controlled_import
    if case == "wrong-sha":
        state["payload"] = b"x" * len(state["payload"])
    elif case == "truncated":
        state["headers"]["Content-Length"] = str(len(state["payload"]) + 1)
    elif case == "oversize-header":
        state["headers"]["Content-Length"] = str(gate.MAX_DATABASE_ARCHIVE_BYTES + 1)
    elif case == "oversize-body":
        state["headers"] = {}
        monkeypatch.setattr(gate, "MAX_DATABASE_ARCHIVE_BYTES", 1)
    else:
        state["redirect"] = "http://example.invalid/fixed.tar.zst"
    with pytest.raises(gate.NativeGateError):
        prepare()
    assert not state["imports"]
    assert evidence["failure_stage"] == "download-archive"
    assert "actual_sha256" in evidence["archive"]


@pytest.mark.parametrize("updates", [
    {"source": "https://unverified.invalid"}, {"client_version": "v6.1.9"},
    {"digest": "sha256:" + "a" * 64}, {"unexpected": True},
])
def test_import_metadata_contract_is_not_arbitrary_self_attestation(controlled_import, updates):
    prepare, state, evidence, _, _, _ = controlled_import
    state["metadata_updates"] = updates
    with pytest.raises(gate.NativeGateError):
        prepare()
    assert not state["statuses"] and evidence["failure_stage"] == "validate-import-metadata"


def test_valid_format_digest_mismatch_is_rejected_by_forced_grype_status(controlled_import):
    prepare, state, evidence, _, _, _ = controlled_import
    state["metadata_updates"] = {"digest": "xxh64:" + "b" * 16}
    with pytest.raises(gate.NativeGateError):
        prepare()
    assert evidence["validated_status"]["valid"] is False


def test_archive_replaced_after_download_is_rejected_before_import(controlled_import, monkeypatch):
    prepare, state, _, _, _, _ = controlled_import
    original = gate._download_database_archive

    def replaced(spec, destination, evidence):
        original(spec, destination, evidence)
        destination.write_bytes(b"replacement after stream verification")

    monkeypatch.setattr(gate, "_download_database_archive", replaced)
    with pytest.raises(gate.NativeGateError):
        prepare()
    assert not state["imports"]


@pytest.mark.parametrize("updates", [
    {"valid": False}, {"schemaVersion": "v6.1.9"}, {"built": "2026-10-27T06:38:44Z"},
    {"path": "/different/database"}, {"from": "remote URL"}, {"error": "checksum mismatch"},
])
def test_status_requires_exact_schema_build_path_source_and_validity(controlled_import, updates):
    prepare, state, evidence, _, _, _ = controlled_import
    state["status_updates"] = updates
    with pytest.raises(gate.NativeGateError):
        prepare()
    assert evidence["failure_stage"] == "validate-status"


@pytest.mark.parametrize("case", ["nonzero-import", "archive-change", "status-db-change"])
def test_failed_import_or_changed_input_output_stays_blocked(controlled_import, case):
    prepare, state, evidence, _, _, _ = controlled_import
    state[{"nonzero-import": "exit", "archive-change": "change_archive", "status-db-change": "change_during_status"}[case]] = 1
    with pytest.raises(gate.NativeGateError):
        prepare()
    assert evidence["failure_stage"] in {"local-import", "after-status"}
    if case == "nonzero-import":
        assert evidence["operations"][0]["exit_code"] == 1
        assert evidence["operations"][0]["stderr_tail"] == "import failed"


@pytest.mark.parametrize("stage", ["after-native-scan", "after-full-scan"])
def test_derived_database_change_after_each_scan_is_rejected(controlled_import, stage):
    prepare, _, evidence, _, _, _ = controlled_import
    prepared = prepare()
    original = prepared.path.read_bytes()
    prepared.path.write_bytes(b"x" * len(original))
    with pytest.raises(gate.NativeGateError):
        prepared.assert_unchanged(stage)
    assert evidence["failure_stage"] == stage


def test_expired_snapshot_never_reaches_import(controlled_import):
    prepare, state, _, _, _, _ = controlled_import
    with pytest.raises(gate.NativeGateError):
        prepare(datetime(2026, 11, 4, tzinfo=timezone.utc))
    assert not state["imports"]


def test_unchanged_db_cannot_hide_a_replaced_import_receipt(controlled_import):
    prepare, _, _, _, _, _ = controlled_import
    prepared = prepare()
    metadata = json.loads(prepared.metadata_path.read_text())
    metadata["source"] = "different"
    prepared.metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(gate.NativeGateError):
        prepared.assert_unchanged("after-full-scan")
