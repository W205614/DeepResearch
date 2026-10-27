"""Cold-copy one owned monitoring data directory into a new named volume.

Inspection is read-only. Snapshot/apply require an already stopped source and
never stop, start, remove or replace it. Original data and private archives stay.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import uuid
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.host_task import hidden_process_options  # noqa: E402

HELPER = "alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a"
PATHS = {"prometheus": "/prometheus", "alertmanager": "/alertmanager"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True, timeout=120,
                            **hidden_process_options())
    if result.returncode:
        raise RuntimeError("Docker operation failed; private diagnostic withheld")
    return result.stdout


def inspect_source(container, project, service, cold=False):
    info = json.loads(run("inspect", container))[0]
    labels = info["Config"]["Labels"] or {}
    if (labels.get("com.docker.compose.project") != project or
            labels.get("com.docker.compose.service") != service or
            not labels.get("com.docker.compose.config-hash")):
        raise ValueError("Source is not the specified real Compose project/service")
    if cold and (info["State"]["Running"] or info["State"].get("Paused")):
        raise ValueError("Cold copy requires an already stopped source")
    path = PATHS[service]
    mounts = [m for m in info["Mounts"] if PurePosixPath(path).is_relative_to(PurePosixPath(m["Destination"]))]
    mount = max(mounts, key=lambda m: len(m["Destination"]), default=None)
    if mount and mount["Type"] != "volume":
        raise ValueError("Review host bind storage separately; this helper accepts volumes or container layers")
    source_volume = mount.get("Name") if mount else None
    if cold and source_volume:
        assert_volume_idle(source_volume, except_container=info["Id"])
    return {"container_id": info["Id"], "image_id": info["Image"], "project": project,
            "service": service, "path": path, "state": info["State"]["Status"],
            "config_hash": labels["com.docker.compose.config-hash"],
            "storage_kind": "volume" if mount else "container_layer", "source_volume": source_volume}


def assert_volume_idle(volume, except_container=None):
    ids = run("ps", "-aq", "--filter", "volume=" + volume).decode().split()
    if ids:
        for item in json.loads(run("inspect", *ids)):
            if item["Id"] != except_container and item["State"]["Running"]:
                if any(m.get("Name") == volume and m.get("RW") for m in item["Mounts"]):
                    raise ValueError("Volume has another running writer")


def manifest(path):
    files = {}
    with tarfile.open(path, "r:gz") as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            if name.is_absolute() or ".." in name.parts or item.issym() or item.islnk():
                raise ValueError("Unsafe archive path or link")
            if item.isdir():
                continue
            if not item.isfile() or name.as_posix() in files:
                raise ValueError("Unexpected or duplicate archive entry")
            stream = archive.extractfile(item)
            files[name.as_posix()] = {"sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                                      "size": item.size, "uid": item.uid, "gid": item.gid,
                                      "mode": item.mode}
    if not files:
        raise ValueError("Empty source is not a verified migration")
    return files


def stream_gzip(command, destination):
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          **hidden_process_options()) as process:
        with gzip.open(destination, "xb") as output:
            shutil.copyfileobj(process.stdout, output, 1024 * 1024)
        if process.wait():
            raise RuntimeError("Cold stream failed; partial archive retained")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "snapshot", "apply"))
    parser.add_argument("--container", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--service", required=True, choices=PATHS)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--target-volume")
    parser.add_argument("--retain-temporary-resources", action="store_true",
                        help="Retain apply copy/verification helpers and record their exact identities; no cleanup")
    return parser.parse_args(argv)


def helper_run_args(args, directory, operation, invocation, source_id, command):
    if not args.retain_temporary_resources:
        return ["run", "--rm", *command], None
    name = f"dr-monitoring-{args.project}-{args.service}-{operation}-{invocation}"
    cidfile = directory / f"helper-{operation}-{invocation}.cid"
    labels = {"com.docker.compose.project": args.project,
              "io.deepresearch.operation": "monitoring-storage-" + operation,
              "io.deepresearch.migration.service": args.service,
              "io.deepresearch.migration.source": source_id,
              "io.deepresearch.migration.invocation": invocation}
    argv = ["run", "--name", name, "--cidfile", str(cidfile)]
    for key, value in labels.items():
        argv.extend(["--label", key + "=" + value])
    argv.extend(command)
    return argv, {"name": name, "operation": operation, "labels": labels,
                  "cidfile": str(cidfile), "creation_argv": ["docker", *argv],
                  "requested_image": HELPER, "container_id": None, "identity_verified": False}


def capture_helper_identity(record):
    """Read only the retained helper's CID and non-secret creation/identity fields."""
    try:
        cidfile = Path(record["cidfile"])
        if not cidfile.exists():
            record["creation_evidence_status"] = "no_cidfile_observed"
            return
        container_id = cidfile.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError("Unexpected helper CID")
        record["container_id"] = container_id
        record["cidfile_sha256"] = sha(cidfile)
        info = json.loads(run("inspect", container_id))[0]
        labels = info["Config"]["Labels"] or {}
        if (info["Id"] != container_id or info["Name"].lstrip("/") != record["name"] or
                any(labels.get(key) != value for key, value in record["labels"].items())):
            raise ValueError("Retained helper identity mismatch")
        record["inspect"] = {"id": info["Id"], "name": info["Name"], "created": info["Created"],
                             "image_id": info["Image"], "state": info["State"]["Status"],
                             "mounts": [{key: mount.get(key) for key in
                                         ("Type", "Name", "Source", "Destination", "RW")}
                                        for mount in info["Mounts"]]}
        record["identity_verified"] = True
        record["creation_evidence_status"] = "cid_and_exact_inspect_identity_verified"
    except Exception as exc:
        # Preserve the CID and original copy failure without leaking Docker diagnostics.
        record["creation_evidence_status"] = "inspection_failed_" + type(exc).__name__


def main():
    args = parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", args.project):
        raise ValueError("Unexpected Compose project name")
    source = inspect_source(args.container, args.project, args.service, cold=args.mode != "inspect")
    if args.mode == "inspect":
        print(json.dumps(source))
        return
    directory = args.directory.resolve() if args.directory else None
    if directory is None or not directory.is_relative_to(ROOT / ".cache"):
        raise ValueError("Private snapshots must stay inside this workspace .cache")
    if args.mode == "snapshot":
        directory.mkdir(parents=True, exist_ok=False)
        payload = directory / "data.tgz"
        stream_gzip(["docker", "cp", source["container_id"] + ":" + source["path"] + "/.", "-"], payload)
        files = manifest(payload)
        assert inspect_source(args.container, args.project, args.service, cold=True) == source
        snapshot = {"source": source, "payload_sha256": sha(payload),
                    "files": files, "original_retained": True}
        (directory / "snapshot.private.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "cold_snapshot_verified", "files": len(files), "original_retained": True}))
        return
    snapshot = json.loads((directory / "snapshot.private.json").read_text(encoding="utf-8"))
    payload = directory / "data.tgz"
    if snapshot["source"] != source or sha(payload) != snapshot["payload_sha256"]:
        raise ValueError("Source identity/state or snapshot bytes changed")
    if manifest(payload) != snapshot["files"]:
        raise ValueError("Snapshot manifest mismatch")
    source_recheck = directory / "source-recheck.tgz"
    stream_gzip(["docker", "cp", source["container_id"] + ":" + source["path"] + "/.", "-"], source_recheck)
    if manifest(source_recheck) != snapshot["files"]:
        raise ValueError("Source data changed since snapshot; take a new snapshot before migration")
    target = args.target_volume
    expected_name = args.project + "_" + args.service + "-data"
    if target != expected_name or target == source["source_volume"]:
        raise ValueError("Target must be the explicit new Compose project monitoring-data volume")
    if run("volume", "ls", "-q", "--filter", "name=^" + target + "$").decode().strip():
        raise ValueError("Target already exists; retain it and review rather than overwrite")
    run("volume", "create", "--label", "com.docker.compose.project=" + args.project,
        "--label", "com.docker.compose.volume=" + args.service + "-data",
        "--label", "io.deepresearch.migration.source=" + source["container_id"], target)
    receipt = {"status": "in_progress", "source": source, "target_volume": target,
               "files": len(snapshot["files"]), "payload_sha256": snapshot["payload_sha256"],
               "original_retained": True, "source_never_started_or_stopped": True,
               "per_file_sha256_size_uid_gid_mode_equal": False,
               "retain_temporary_resources": args.retain_temporary_resources, "temporary_helpers": []}
    receipt_path = directory / "migration-receipt.json"
    invocation = uuid.uuid4().hex
    try:
        command, helper = helper_run_args(args, directory, "copy", invocation, source["container_id"], [
            "--network", "none", "--user", "0", "--mount",
            "type=bind,source=" + str(payload) + ",target=/snapshot.tgz,readonly", "--mount",
            "type=volume,source=" + target + ",target=/data", HELPER,
            "sh", "-ec", "test -z \"$(ls -A /data)\"; tar -xpf /snapshot.tgz -C /data; "
            "chown 65534:65534 /data; chmod 0750 /data"])
        if helper:
            receipt["temporary_helpers"].append(helper)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        run(*command)
        copied = directory / "target-verification.tgz"
        command, helper = helper_run_args(args, directory, "verify", invocation, source["container_id"], [
            "--network", "none", "--user", "0", "--mount",
            "type=volume,source=" + target + ",target=/data,readonly", HELPER,
            "tar", "-cf", "-", "-C", "/data", "."])
        if helper:
            receipt["temporary_helpers"].append(helper)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        stream_gzip(["docker", *command], copied)
        if manifest(copied) != snapshot["files"]:
            raise ValueError("Copied file bytes or per-file ownership/mode mismatch; retain both copies")
        assert inspect_source(args.container, args.project, args.service, cold=True) == source
        receipt.update(status="verified", per_file_sha256_size_uid_gid_mode_equal=True)
    except BaseException:
        receipt["status"] = "failed"
        raise
    finally:
        if args.retain_temporary_resources:
            for helper in receipt["temporary_helpers"]:
                capture_helper_identity(helper)
            if receipt["status"] == "verified" and not all(
                    helper["identity_verified"] for helper in receipt["temporary_helpers"]):
                receipt["status"] = "helper_identity_incomplete"
        if args.retain_temporary_resources or receipt["status"] == "verified":
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if receipt["status"] != "verified":
        raise RuntimeError("Helper identity could not be fully recorded; resources and receipt retained")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
