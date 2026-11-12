"""Add an explicitly authenticated local archive transport; preserve the controlled Grype import gates."""
from pathlib import Path
import json
import re
import shutil
import stat
import tempfile
from urllib.parse import parse_qsl, urlsplit

from scripts import check_native_jvm as original
from scripts.check_native_jvm import (
    MAX_DATABASE_ARCHIVE_BYTES, NativeGateError, PreparedDatabase, current_database_snapshot,
    validate_snapshot_age, digest, _database_fingerprint, _database_command,
    _save_database_evidence, _unique_pairs,
)


def copy_verified_archive(spec, source, destination, evidence):
    parsed = urlsplit(spec['url'])
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or [v for k, v in query if k == 'checksum'] != ['sha256:' + spec['archive_sha256']]
            or not re.fullmatch(r'[0-9a-f]{64}', spec['archive_sha256'])):
        raise NativeGateError('Pinned archive URL/checksum is invalid')
    if (source.is_symlink() or not source.is_file() or not stat.S_ISREG(source.lstat().st_mode)
            or not 0 < source.stat().st_size <= MAX_DATABASE_ARCHIVE_BYTES):
        raise NativeGateError('The explicit archive transport is not a bounded regular file')
    before = _database_fingerprint(source)
    if before['sha256'] != spec['archive_sha256']:
        raise NativeGateError('The complete transport archive differs from the current publisher snapshot')
    with source.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    copied = _database_fingerprint(destination)
    if _database_fingerprint(source) != before or copied != before:
        raise NativeGateError('Authenticated archive changed during its controlled copy')
    evidence['archive'] = {'identity_url': spec['url'], 'transport': 'explicit_preverified_archive',
        'source': str(source), 'expected_sha256': spec['archive_sha256'],
        'actual_sha256': copied['sha256'], 'actual_bytes': copied['bytes'],
        'max_bytes': MAX_DATABASE_ARCHIVE_BYTES, 'verified': True,
        'existing_hydrated_database_reused': False}


def prepare_database(grype, spec, cache_root, output_dir, env, evidence, *, now=None, archive_transport=None):
    """Authenticate the immutable archive, then attest one pinned-tool local import."""
    if archive_transport is None:
        return original.prepare_database(grype, spec, cache_root, output_dir, env, evidence, now=now)
    evidence.update(stage="resolve-snapshot", identity="immutable-archive-controlled-import")
    try:
        if "distribution_url" in spec:
            spec = current_database_snapshot(spec, output_dir, evidence, now=now)
        evidence.update(stage="validate-snapshot",
                        snapshot={k: spec[k] for k in ("url", "archive_sha256", "schema", "built", "max_snapshot_age_days")},
                        historical_hydrated_reference={"sha256": spec["db_sha256"], "bytes": spec["db_bytes"],
                                                       "role": "historical observation, not the derived-byte gate"})
        validate_snapshot_age(spec, now=now)
        evidence["import_tool"] = {"path": str(grype), "sha256": digest(grype)}
        cache_root.mkdir(parents=True, exist_ok=True)
        fresh = Path(tempfile.mkdtemp(prefix="verified-import-", dir=cache_root)).resolve()
        db = fresh / "6/vulnerability.db"
        evidence.update(fresh_cache=str(fresh), preexisting_database_reused=False)
        if db.exists():
            raise NativeGateError("Fresh database import directory was not empty")
        active_env = {k: v for k, v in env.items() if not k.startswith("GRYPE_")}
        active_env.update(GRYPE_DB_CACHE_DIR=str(fresh), GRYPE_DB_AUTO_UPDATE="false",
                          GRYPE_DB_VALIDATE_BY_HASH_ON_START="true", GRYPE_DB_VALIDATE_AGE="true",
                          GRYPE_CHECK_FOR_APP_UPDATE="false")
        config = output_dir / "database-config.json"
        config.write_text(json.dumps({"db": {"cache-dir": str(fresh), "auto-update": False,
                                              "validate-by-hash-on-start": True, "validate-age": True},
                                      "check-for-app-update": False}), encoding="utf-8")
        archive = output_dir / "pinned-advisory-db.tar.zst"
        evidence["stage"] = "download-archive"
        copy_verified_archive(spec, Path(archive_transport), archive, evidence)
        archive_fingerprint = _database_fingerprint(archive)
        if (archive_fingerprint["sha256"] != spec["archive_sha256"]
                or archive_fingerprint["bytes"] != evidence["archive"]["actual_bytes"]):
            raise NativeGateError("Verified archive changed before local import")
        _database_command([grype, "--config", config, "db", "import", archive.resolve()],
                          env=active_env, evidence=evidence, stage="local-import", timeout=900)
        if _database_fingerprint(archive) != archive_fingerprint:
            raise NativeGateError("Verified archive changed during local import")
        evidence["stage"] = "validate-import-metadata"
        metadata_path = fresh / "6/import.json"
        if (db.resolve() != db or metadata_path.resolve() != metadata_path or not metadata_path.is_file()
                or not stat.S_ISREG(metadata_path.lstat().st_mode) or metadata_path.stat().st_size > 65536):
            raise NativeGateError("Imported database metadata/path is unsafe")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        evidence["import_metadata"] = metadata
        if (set(metadata) != {"digest", "source", "client_version"}
                or metadata.get("source") != "manual import" or metadata.get("client_version") != spec["schema"]
                or not re.fullmatch(r"xxh64:[0-9a-f]{16}", metadata.get("digest", ""))):
            raise NativeGateError("Controlled import metadata differs from the pinned client/schema")
        prepared = PreparedDatabase(db, spec, active_env, evidence, output_dir, now=now)
        status = json.loads(_database_command([grype, "--config", config, "db", "status", "-o", "json"],
                                              env=active_env, evidence=evidence, stage="validate-status"))
        evidence["validated_status"] = status
        if (status.get("valid") is not True or status.get("error")
                or status.get("schemaVersion") != spec["schema"] or status.get("built") != spec["built"]
                or status.get("from") != "manual import" or status.get("path") != str(db)):
            raise NativeGateError("Controlled database status/path/schema/built differs")
        # Grype status with forced checksum validation verifies actual DB against import.json's xxh64.
        prepared.assert_unchanged("after-status")
        evidence["stage"] = "prepared"
        return prepared
    except Exception as error:
        evidence.update(failure_stage=evidence.get("stage"), error_type=type(error).__name__)
        raise
    finally:
        _save_database_evidence(evidence, output_dir)
