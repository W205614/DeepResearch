"""Verify actual JRE bytes, JVM inventory, and native/full CPE scans; never start a service."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import tarfile
import uuid
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "deployment/security/native-jvm-tools.json"


class NativeGateError(ValueError):
    pass


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def run(args, *, env=None, timeout=300):
    result = subprocess.run([str(a) for a in args], capture_output=True, env=env, timeout=timeout, check=False)
    if result.returncode:
        raise NativeGateError("A required native inventory/scanner operation failed")
    return result.stdout


def executable(tool, lock, cache, tool_dir):
    spec = lock["tools"][tool]
    if platform.system() == "Windows":
        path = tool_dir / (tool + ".exe")
        if digest(path) != spec["windows_amd64"]["executable_sha256"]:
            raise NativeGateError("Pinned native scanner executable changed")
        return path
    if platform.system() != "Linux" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise NativeGateError("Native scanner platform is not pinned")
    info = spec["linux_amd64"]
    archive = cache / (tool + "-" + spec["version"] + ".tar.gz")
    cache.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        with urlopen(info["url"], timeout=60) as response, archive.open("xb") as output:
            received = 0
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > info["archive_bytes"]:
                    raise NativeGateError("Native scanner archive exceeds its bound")
                output.write(block)
    if archive.stat().st_size != info["archive_bytes"] or digest(archive) != info["archive_sha256"]:
        raise NativeGateError("Native scanner archive is incomplete or changed; preserve for investigation")
    with tarfile.open(archive) as source:
        candidates = [m for m in source if m.name == tool]
        if len(candidates) != 1 or not candidates[0].isfile() or candidates[0].size > 200 * 1024 * 1024:
            raise NativeGateError("Native scanner executable inventory is invalid")
        data = source.extractfile(candidates[0]).read()
    path = cache / tool
    if path.exists() and path.read_bytes() != data:
        raise NativeGateError("Cached scanner executable differs from verified archive")
    if not path.exists():
        path.write_bytes(data)
        path.chmod(0o755)
    return path


def validate_database(path, spec, *, now=None):
    now = now or datetime.now(timezone.utc)
    built = datetime.fromisoformat(spec["built"].replace("Z", "+00:00"))
    age = (now - built).total_seconds()
    if age < -300 or age > spec["max_snapshot_age_days"] * 86400:
        raise NativeGateError("Pinned advisory snapshot is stale or in the future; explicitly refresh its lock")
    if not path.is_file() or path.stat().st_size != spec["db_bytes"] or digest(path) != spec["db_sha256"]:
        raise NativeGateError("Pinned native advisory database bytes differ")


def validate_jvm(sbom, profile, expected):
    artifacts = sbom.get("artifacts", [])
    jvms = [a for a in artifacts if a.get("foundBy") == "java-jvm-cataloger"]
    if len(artifacts) != 1 or len(jvms) != 1:
        raise NativeGateError("Exactly one actual JVM must be identified; missing or extra runtimes block")
    artifact = jvms[0]
    release = artifact.get("metadata", {}).get("release", {})
    required = {"javaVersion": expected["java_version"], "semanticVersion": expected["semantic_version"],
                "implementor": expected["implementor"], "sourceRepo": expected["source_repo"],
                "libc": profile["libc"], "osArch": "x86_64", "osName": "Linux", "imageType": "JRE"}
    if (artifact.get("type") != "binary" or artifact.get("metadataType") != "java-jvm-installation"
            or artifact.get("version") != expected["version"]
            or any(release.get(k) != v for k, v in required.items())
            or not any(c.get("cpe") == expected["cpe"] and c.get("source") == "declared" for c in artifact.get("cpes", []))
            or not any(p.get("path") == profile["release_path"] for p in artifact.get("locations", []))):
        raise NativeGateError("Actual JVM version/source/ABI/location/CPE differs from its pinned profile")
    return {"name": artifact["name"], "version": artifact["version"], "cpe": expected["cpe"]}


def verify_jre_tar(stream, expected):
    observed = {}
    total = 0
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            name = member.name.removeprefix("./").rstrip("/")
            if member.isdir() or name in {"", "."}:
                continue
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or name in observed:
                raise NativeGateError("Actual JRE file inventory is unsafe or ambiguous")
            if member.issym():
                observed[name] = {"type": "symlink", "target": member.linkname}
            elif member.isfile():
                total += member.size
                if total > 400 * 1024 * 1024 or len(observed) > 2000:
                    raise NativeGateError("Actual JRE file inventory exceeds its bound")
                hashed = hashlib.sha256()
                source = archive.extractfile(member)
                while block := source.read(1024 * 1024):
                    hashed.update(block)
                observed[name] = {"type": "file", "sha256": hashed.hexdigest(), "bytes": member.size}
            else:
                raise NativeGateError("Unexpected actual JRE file type")
    if observed != expected:
        raise NativeGateError("Actual JRE binary/resource bytes differ from official fixed archive")
    return {"verified_entries": len(observed), "verified_regular_bytes": total}


def validate_source(sbom, inspection):
    source = sbom.get("source", {}).get("metadata", {})
    if sbom.get("source", {}).get("type") != "image" or source.get("userInput") != inspection.get("Id"):
        raise NativeGateError("Syft inventory lacks the actual immutable image source")
    config_bytes = base64.b64decode(source.get("config", ""), validate=True)
    manifest_bytes = base64.b64decode(source.get("manifest", ""), validate=True)
    if not config_bytes or not manifest_bytes or max(len(config_bytes), len(manifest_bytes)) > 4 * 1024 * 1024:
        raise NativeGateError("Actual image source config/manifest is missing or oversized")
    config_id = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    manifest_id = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    config, manifest = json.loads(config_bytes), json.loads(manifest_bytes)
    if (source.get("imageID") != config_id or source.get("manifestDigest") != manifest_id
            or manifest.get("config", {}).get("digest") != config_id
            or config.get("rootfs", {}).get("diff_ids") != inspection.get("RootFS", {}).get("Layers")
            or config.get("architecture") != "amd64" or config.get("os") != "linux"
            or inspection.get("Architecture") != "amd64" or inspection.get("Os") != "linux"):
        raise NativeGateError("Syft config/manifest hash chain differs from actual inspected filesystem")
    for key in ("User", "Env", "Entrypoint", "WorkingDir"):
        if config.get("config", {}).get(key) != inspection.get("Config", {}).get(key):
            raise NativeGateError("Syft image runtime configuration differs from actual inspection")


def validate_full_inventory(sbom, profile):
    artifacts = sbom.get("artifacts", [])
    if not artifacts or any(not a.get("name") or not a.get("version") or a["version"] in {"unknown", "(devel)"} for a in artifacts):
        raise NativeGateError("Full inventory package/version is unknown")
    counts = Counter(a.get("type") for a in artifacts)
    if any(counts[key] < count for key, count in profile["minimum_packages"].items()):
        raise NativeGateError("Full JVM/image inventory coverage is below its reviewed package baseline")


def validate_scan(report, sbom):
    descriptor = report.get("descriptor", {})
    image_id = sbom.get("source", {}).get("metadata", {}).get("imageID")
    if (not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or descriptor.get("name") != "grype" or descriptor.get("version") != "0.121.0"
            or not isinstance(report.get("matches"), list)
            or descriptor.get("db", {}).get("status", {}).get("valid") is not True):
        raise NativeGateError("Native scanner report or valid advisory DB evidence is missing")
    if report.get("source", {}).get("target", {}).get("imageID") != sbom.get("source", {}).get("metadata", {}).get("imageID"):
        raise NativeGateError("Scanner report does not bind to the actual image inventory")
    if report.get("ignoredMatches"):
        raise NativeGateError("Native scanner ignored findings are not authorized")
    counts = Counter({"High": 0, "Critical": 0, "Unknown": 0, "Medium": 0, "Low": 0, "Negligible": 0})
    for item in report["matches"]:
        severity = item.get("vulnerability", {}).get("severity")
        if severity not in counts:
            raise NativeGateError("Native scanner finding severity is unknown")
        counts[severity] += 1
    if counts["High"] or counts["Critical"] or counts["Unknown"]:
        raise NativeGateError("Native/full image scan has blocking HIGH/CRITICAL/UNKNOWN findings")
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--profile", required=True, choices=["business-java", "keycloak"])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tool-dir", type=Path, default=ROOT / ".cache/native-jvm/tools")
    parser.add_argument("--db-cache", type=Path, default=ROOT / ".cache/native-jvm/grype-db")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary = {"passed": False, "image": args.image, "profile": args.profile, "services_started": False}
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        profile = lock["profiles"][args.profile]
        manifest = ROOT / profile["file_manifest"]
        if digest(manifest) != profile["file_manifest_sha256"]:
            raise NativeGateError("Official JRE file manifest changed")
        syft = executable("syft", lock, args.tool_dir, args.tool_dir)
        grype = executable("grype", lock, args.tool_dir, args.tool_dir)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SYFT_", "GRYPE_"))}
        env.update(GRYPE_DB_CACHE_DIR=str(args.db_cache.resolve()), GRYPE_DB_AUTO_UPDATE="false", GRYPE_CHECK_FOR_APP_UPDATE="false", SYFT_CHECK_FOR_APP_UPDATE="false")
        db = args.db_cache / "6/vulnerability.db"
        if not db.exists():
            run([grype, "db", "import", lock["database"]["url"]], env=env, timeout=900)
        validate_database(db, lock["database"])
        status = json.loads(run([grype, "db", "status", "-o", "json"], env=env))
        if status.get("valid") is not True or status.get("schemaVersion") != lock["database"]["schema"]:
            raise NativeGateError("Pinned database status/schema is invalid")
        summary["database"] = {**lock["database"], "validated_status": status}
        inspection = json.loads(run(["docker", "image", "inspect", args.image]))[0]
        image = inspection["Id"]
        summary["actual_image_id"] = image
        name = "dr-native-jvm-inventory-" + uuid.uuid4().hex[:12]
        run(["docker", "create", "--name", name, "--label", "com.deepresearch.purpose=native-jvm-inventory", "--network", "none", "--entrypoint", "/bin/true", image])
        summary["created_audit_container_retained"] = name
        base = str(PurePosixPath(profile["release_path"]).parent)
        process = subprocess.Popen(["docker", "cp", name + ":" + base + "/.", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            summary["actual_JRE_bytes"] = verify_jre_tar(process.stdout, json.loads(manifest.read_text(encoding="utf-8"))["entries"])
            if process.wait(timeout=30):
                raise NativeGateError("Could not read actual JRE files")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        configuration = args.output_dir / "grype-config.json"
        configuration.write_text(json.dumps({"ignore": [], "only-fixed": False, "only-notfixed": False, "check-for-app-update": False}), encoding="utf-8")
        syft_configuration = args.output_dir / "syft-config.json"
        syft_configuration.write_text(json.dumps({"exclude": [], "check-for-app-update": False}), encoding="utf-8")
        for mode in ("native", "full"):
            sbom_path = args.output_dir / (mode + "-syft.json")
            command = [syft, "--config", syft_configuration, "scan", "docker:" + image, "-o", "syft-json=" + str(sbom_path), "--quiet"]
            if mode == "native":
                command += ["--override-default-catalogers", "java-jvm-cataloger"]
            run(command, env=env)
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            validate_source(sbom, inspection)
            if mode == "native":
                summary["JVM"] = validate_jvm(sbom, profile, lock["expected_jvm"])
            else:
                validate_full_inventory(sbom, profile)
            report_path = args.output_dir / (mode + "-grype.json")
            run([grype, "--config", configuration, "sbom:" + str(sbom_path), "-o", "json", "--file", report_path, "--quiet"], env=env)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            summary[mode] = {"packages": len(sbom["artifacts"]), "raw_counts": validate_scan(report, sbom), "sbom_sha256": digest(sbom_path), "report_sha256": digest(report_path)}
        summary["passed"] = True
    except Exception as error:
        summary["error_type"] = type(error).__name__
        if isinstance(error, NativeGateError):
            summary["error"] = str(error)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("passed", "actual_image_id", "error_type", "error") if key in summary}))
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
