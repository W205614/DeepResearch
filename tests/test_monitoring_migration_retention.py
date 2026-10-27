"""Only construct CLI/helper argv; these tests never call Docker or migrate data."""
from scripts.migrate_monitoring_storage import HELPER, helper_run_args, parse_args


CLI = ["apply", "--container", "old-prometheus", "--project", "deepresearch",
       "--service", "prometheus", "--directory", ".cache/migration-test",
       "--target-volume", "deepresearch_prometheus-data"]
SOURCE = "a" * 64


def test_default_copy_and_verification_argv_still_auto_remove(tmp_path):
    args = parse_args(CLI)
    for operation in ("copy", "verify"):
        body = ["--network", "none", HELPER, "true"]
        command, record = helper_run_args(args, tmp_path, operation, "b" * 32, SOURCE, body)
        assert command == ["run", "--rm", *body]
        assert record is None


def test_retained_copy_and_verification_argv_have_unique_owned_creation_records(tmp_path):
    args = parse_args([*CLI, "--retain-temporary-resources"])
    records = []
    for operation in ("copy", "verify"):
        command, record = helper_run_args(
            args, tmp_path, operation, "b" * 32, SOURCE, ["--network", "none", HELPER, "true"])
        assert "--rm" not in command
        assert command[command.index("--name") + 1] == record["name"]
        assert command[command.index("--cidfile") + 1] == record["cidfile"]
        assert "com.docker.compose.project=deepresearch" in command
        assert "io.deepresearch.operation=monitoring-storage-" + operation in command
        assert "io.deepresearch.migration.source=" + SOURCE in command
        assert record["creation_argv"] == ["docker", *command]
        assert record["container_id"] is None and not record["identity_verified"]
        assert record["requested_image"] == HELPER
        records.append(record)
    assert records[0]["name"] != records[1]["name"]
    assert records[0]["cidfile"] != records[1]["cidfile"]
    _, next_record = helper_run_args(args, tmp_path, "copy", "c" * 32, SOURCE, [HELPER, "true"])
    assert next_record["name"] != records[0]["name"]
