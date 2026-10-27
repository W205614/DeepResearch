"""Exercise a Grafana upgrade on a checksummed backup copy, never a live volume.

Resources are retained with ownership labels for the later coordinated cleanup.
Only synthetic credentials are used. This does not establish image security.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import tarfile
import time
import re
import sqlite3


_TARGETS = {}


def docker(*args, timeout=120):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()


def api(port, path, credentials=None, data=None):
    command = ["docker", "exec", _TARGETS[port], "wget", "-S", "-O", "-", "-T", "10"]
    if credentials:
        token = base64.b64encode(":".join(credentials).encode()).decode()
        command.extend(["--header", "Authorization: Basic " + token])
    if data is not None:
        command.extend(["--header", "Content-Type: application/json", "--post-data", json.dumps(data)])
    command.append("http://127.0.0.1:3000/" + path)
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=20)
    codes = re.findall(r"HTTP/\S+ (\d{3})", result.stderr)
    if not codes:
        raise OSError("Copied Grafana is not accepting local requests")
    status = int(codes[-1])
    return status, json.loads(result.stdout) if status < 300 and result.stdout else None


def wait_ready(port):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            status, body = api(port, "api/health")
            if status == 200 and body.get("database") == "ok":
                return body.get("version")
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("Grafana copy did not become ready within 180 seconds")


def snapshot(port, credentials):
    status, dashboards = api(port, "api/search?type=dash-db", credentials)
    assert status == 200
    result = {"dashboards": {}}
    for entry in dashboards:
        status, body = api(port, "api/dashboards/uid/" + entry["uid"], credentials)
        assert status == 200
        dashboard = body["dashboard"]
        # Preserve functional panels/queries/variables; ignore migration version counters.
        panels = [{k: panel.get(k) for k in ("id", "type", "title", "targets", "datasource")}
                  for panel in dashboard.get("panels", [])]
        material = {"title": dashboard["title"], "panels": panels,
                    "templating": dashboard.get("templating")}
        result["dashboards"][entry["uid"]] = hashlib.sha256(
            json.dumps(material, sort_keys=True).encode()).hexdigest()
    status, sources = api(port, "api/datasources", credentials)
    assert status == 200
    result["datasources"] = sorted((x["uid"], x["type"], x["url"], x["access"])
                                   for x in sources)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--current-image", default="grafana/grafana:11.5.2")
    parser.add_argument("--candidate-image", default="grafana/grafana:13.2.4")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-plugin-signatures", action="store_true")
    parser.add_argument("--verify-datasource-components", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new evidence filename; previous attempts are retained")
    private_dir = Path(__file__).resolve().parents[1] / ".cache/grafana-upgrade" / args.output.stem
    private_dir.mkdir(parents=True, exist_ok=False)
    archive = args.archive.resolve()
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    payload = archive / "grafana-data.tgz"
    expected = manifest["files"][payload.name]
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == expected
    with tarfile.open(payload, "r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.islnk() or member.issym():
                raise ValueError("Only regular, relative backup entries are accepted")
        database_member = next(x for x in tar.getmembers() if x.name.endswith("/grafana.db"))
        private_database = private_dir / "source.db"
        private_database.write_bytes(tar.extractfile(database_member).read())
    with sqlite3.connect(f"file:{private_database.resolve().as_posix()}?mode=ro", uri=True) as source_db:
        assert source_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        expected_dashboards = source_db.execute("SELECT count(*) FROM dashboard WHERE is_folder=0").fetchone()[0]
    provisioning = archive / "binds/0001"
    for path in provisioning.rglob("*"):
        if path.is_file():
            relative = path.relative_to(archive).as_posix()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["files"][relative]
    suffix = time.strftime("%Y%m%d-%H%M%S")
    prefix = "dr-opt-grafana-" + suffix
    network, volume = prefix + "-network", prefix + "-data"
    label = "io.deepresearch.validation=optimization-20261026"
    docker("network", "create", "--internal", "--label", label, network)
    docker("volume", "create", "--label", label, volume)
    docker("run", "--rm", "--network", "none", "--mount",
           f"type=bind,source={payload},target=/backup.tgz,readonly", "--mount",
           f"type=volume,source={volume},target=/data", "alpine:3.24.3",
           "tar", "-xzf", "/backup.tgz", "-C", "/data")
    password, viewer_password = secrets.token_hex(24), secrets.token_hex(24)
    admin, viewer = ("admin", password), ("upgrade-fixture-viewer", viewer_password)
    docker("run", "--rm", "--network", "none", "--user", "472", "--mount",
           f"type=volume,source={volume},target=/var/lib/grafana", "--entrypoint",
           "/usr/share/grafana/bin/grafana", args.current_image, "cli", "--homepath",
           "/usr/share/grafana", "admin", "reset-admin-password", password)
    containers = []
    receipt = {"scope": "copied Grafana component only; no live or daily volume mounted",
               "backup_component_sha256": expected, "backup_source_sha": manifest.get("source_sha"),
               "resources_retained": {"network": network, "volume": volume},
               "images": {}, "status": "failed", "security_pass": False}
    try:
        if args.verify_datasource_components:
            prometheus_config = private_dir / "prometheus.yml"
            prometheus_config.write_text("global:\n  scrape_interval: 15s\nscrape_configs: []\n", encoding="utf-8")
            component_specs = [
                ("prometheus", "prom/prometheus:v3.15.1", prometheus_config, "/etc/fixture.yml", ["--config.file=/etc/fixture.yml"], None),
                ("loki", "grafana/loki:3.7.9", Path(__file__).resolve().parents[1] / "observability/loki.yaml", "/etc/fixture.yml", ["-config.file", "/etc/fixture.yml"], "/loki"),
                ("tempo", "grafana/tempo:2.10.10", Path(__file__).resolve().parents[1] / "observability/tempo.yaml", "/etc/fixture.yml", ["-config.file", "/etc/fixture.yml"], "/var/tempo"),
            ]
            for alias, image, config_file, target, commands, data_path in component_specs:
                name = prefix + "-" + alias
                command = ["run", "-d", "--name", name, "--label", label, "--network", network,
                           "--network-alias", alias, "--memory", "384m", "--cpus", ".5", "--pids-limit", "128",
                           "--mount", f"type=bind,source={config_file},target={target},readonly"]
                if data_path:
                    command.extend(["--tmpfs", data_path + ":uid=10001,gid=10001,mode=0755"])
                docker(*command, image, *commands)
                containers.append(name)

        def start(image, name, port):
            receipt["images"][name] = json.loads(docker("image", "inspect", image))[0]["Id"]
            args_run = ["run", "-d", "--name", name, "--label", label, "--network", network,
                        "--memory", "1g", "--cpus", "1", "--pids-limit", "256", "--mount",
                        f"type=volume,source={volume},target=/var/lib/grafana", "--mount",
                        f"type=bind,source={provisioning},target=/etc/grafana/provisioning,readonly"]
            for key, value in {"GF_ANALYTICS_REPORTING_ENABLED": "false",
                               "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
                               "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
                               "GF_UNIFIED_ALERTING_ENABLED": "false",
                               "GF_PLUGINS_PREINSTALL_DISABLED": "true",
                               "GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED": "true",
                               "GF_AUTH_ANONYMOUS_ENABLED": "false",
                               "GF_USERS_ALLOW_SIGN_UP": "false",
                               "GF_UNIFIED_STORAGE_MIGRATION_CACHE_SIZE_KB": "65536",
                               "GF_UNIFIED_STORAGE_MIGRATION_PARQUET_BUFFER": "true"}.items():
                args_run.extend(["-e", key + "=" + value])
            docker(*args_run, image)
            containers.append(name)
            _TARGETS[port] = name
            return wait_ready(port)

        old_name, new_name = prefix + "-old", prefix + "-new"
        receipt["old_version"] = start(args.current_image, old_name, 28031)
        before = snapshot(28031, admin)
        assert len(before["dashboards"]) == expected_dashboards, "Source dashboard was not visible before upgrade"
        status, _ = api(28031, "api/admin/users", admin,
                        {"name": "Upgrade fixture", "login": viewer[0],
                         "email": "upgrade-fixture@example.invalid", "password": viewer_password})
        assert status == 200
        docker("stop", "--time", "20", old_name)
        receipt["new_version"] = start(args.candidate_image, new_name, 28032)
        after = snapshot(28032, admin)
        assert before == after, "Dashboard or datasource functional snapshot changed"
        if args.check_plugin_signatures:
            status, registered = api(28032, "api/plugins", admin)
            assert status == 200
            registered = {item["id"]: item for item in registered}
            vendor = Path(__file__).resolve().parents[1] / "deployment/vendors/grafana"
            official = json.loads((vendor / "official-plugins.lock.json").read_text(encoding="utf-8"))
            rebuilt = {x["plugin_directory"] for x in json.loads((vendor / "plugin-modules.json").read_text(encoding="utf-8"))}
            signatures = {}
            for plugin in official:
                record = registered[plugin["slug"]]
                summary = {key: record.get(key) for key in ("signature", "signatureType", "signatureOrg")}
                signatures[plugin["slug"]] = summary
                assert summary["signature"] == "valid", "Plugin did not pass upstream signature and exact hash validation"
                if plugin["slug"] in rebuilt:
                    assert summary["signatureType"] == "private"
                    assert summary["signatureOrg"] == "DeepResearch maintainer rebuild"
            receipt["runtime_plugin_signatures"] = signatures
        if args.verify_datasource_components:
            status, sources = api(28032, "api/datasources", admin)
            assert status == 200
            health = {}
            for item in sources:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    status, body = api(28032, "api/datasources/uid/" + item["uid"] + "/health", admin)
                    if status == 200 and body.get("status") == "OK":
                        break
                    time.sleep(2)
                assert status == 200 and body.get("status") == "OK", "Real datasource health failed: " + item["type"]
                health[item["type"]] = status
            prometheus = next(x for x in sources if x["type"] == "prometheus")
            status, body = api(28032, "api/ds/query", admin,
                               {"from": "now-5m", "to": "now", "queries": [
                                   {"refId": "A", "datasource": {"type": "prometheus", "uid": prometheus["uid"]},
                                    "expr": "vector(1)", "instant": True, "range": False,
                                    "intervalMs": 1000, "maxDataPoints": 10}]})
            assert status == 200
            values = [v for frame in body["results"]["A"]["frames"] for column in frame["data"]["values"] for v in column]
            assert 1 in values, "Prometheus plugin did not return the actual constant query result"
            receipt["actual_datasource_health"] = health
            receipt["actual_prometheus_query"] = "vector(1) returned1 through the actual Grafana plugin and Prometheus3.15.1"
        assert api(28032, "api/users", admin)[0] == 200, "Administrative test endpoint is unavailable"
        receipt["preserved_dashboard_count"] = len(before["dashboards"])
        receipt["preserved_datasource_count"] = len(before["datasources"])
        receipt["functional_snapshot_sha256"] = hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()).hexdigest()
        statuses = {"anonymous_search": api(28032, "api/search")[0],
                    "viewer_search": api(28032, "api/search", viewer)[0],
                    "viewer_users": api(28032, "api/users", viewer)[0],
                    "viewer_datasource_create": api(28032, "api/datasources", viewer,
                                                     {"name": "must-deny", "type": "prometheus"})[0]}
        checks = {"anonymous_search_denied": statuses["anonymous_search"] in (401, 403),
                  "viewer_can_read_dashboards": statuses["viewer_search"] == 200,
                  "viewer_cannot_list_users": statuses["viewer_users"] == 403,
                  "viewer_cannot_create_datasource": statuses["viewer_datasource_create"] == 403}
        receipt["authorization_statuses"] = statuses
        receipt["authorization"] = checks
        assert all(checks.values()), "Copied-instance authorization check failed"
        receipt["status"] = "compatibility_passed_security_pending"
        receipt["rollback"] = "original checksummed backup unchanged; restore it, never downgrade migrated volume"
    finally:
        for name in containers:
            docker("stop", "--time", "20", name)
            log = docker("logs", name)
            private_log = private_dir / (name + ".private.log")
            private_log.write_text(log, encoding="utf-8")
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == expected
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "evidence": str(args.output)}))


if __name__ == "__main__":
    main()
