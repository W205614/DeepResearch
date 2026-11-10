"""Adjudicate one reviewed Agent application scope; the publication default stays raw-zero."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_image_secrets import assess  # noqa: E402

PROFILE_NAME = "agent-full-scope-20261110"
PROFILE_PATH = ROOT / "deployment/security/agent-full-scope-20261110.json"
PROFILE_SHA256 = "87602c1f941fc7c8a98ec741bc3470e392a21d3f1902fe2c7ec643ba551ff35c"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def load_profile(name):
    require(name == PROFILE_NAME, "Unknown exact technical review profile")
    content = PROFILE_PATH.read_bytes()
    require(hashlib.sha256(content).hexdigest() == PROFILE_SHA256, "Reviewed Agent profile changed")
    profile = json.loads(content)
    require(
        profile["schema"] == "agent-exact-full-scope-profile-v1" and profile["risk_acceptance"] is False,
        "Technical fixed-code/component review required",
    )
    evidence = profile["source_review_evidence"]
    require(
        hashlib.sha256((ROOT / evidence["path"]).read_bytes()).hexdigest() == evidence["sha256"],
        "Reviewed technical source evidence changed",
    )
    return profile


def finding_tuple(result, finding):
    return {
        "class": result["Class"],
        "type": result["Type"],
        "id": finding["VulnerabilityID"],
        "package": finding["PkgName"],
        "version": finding["InstalledVersion"],
        "package_id": finding.get("PkgID"),
        "purl": finding.get("PkgIdentifier", {}).get("PURL"),
        "severity": finding["Severity"],
        "status": finding.get("Status"),
        "data_source": finding.get("DataSource", {}),
    }


def package_tuple(result, package):
    return {
        "class": result["Class"],
        "type": result["Type"],
        "name": package["Name"],
        "version": package["Version"],
        "purl": package.get("Identifier", {}).get("PURL"),
        "architecture": package.get("Arch"),
        "source": package.get("SrcName"),
        "source_version": package.get("SrcVersion"),
        "file_path": package.get("FilePath"),
    }


def verify_actual_config(raw, inspection):
    require(isinstance(raw, bytes), "Actual portable config bytes required")
    config = json.loads(raw)
    require(
        config["os"] == inspection["Os"]
        and config["architecture"] == inspection["Architecture"]
        and config["rootfs"]["type"] == "layers"
        and config["rootfs"]["diff_ids"] == inspection["RootFS"]["Layers"]
        and config["config"] == inspection["Config"],
        "Actual saved config differs from immutable Engine image",
    )
    return config, "sha256:" + hashlib.sha256(raw).hexdigest()


def capture_actual_config(inspection):
    """Stream Docker's actual immutable image archive; retain only bounded JSON members."""
    image_id = inspection["Id"]
    process = subprocess.Popen(
        ["docker", "image", "save", image_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    small_members = {}
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if member.isfile() and member.size <= 4 * 1024 * 1024:
                    require(member.name not in small_members, "Duplicate saved-image metadata member")
                    small_members[member.name] = archive.extractfile(member).read()
        stderr = process.stderr.read()
        require(
            process.wait(timeout=120) == 0, "Actual image save failed: " + stderr.decode(errors="replace")
        )
    finally:
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    entries = json.loads(small_members["manifest.json"])
    require(len(entries) == 1, "Single actual image config required")
    config_name = entries[0]["Config"]
    raw = small_members[config_name]
    _, config_digest = verify_actual_config(raw, inspection)
    digest = config_digest.removeprefix("sha256:")
    require(
        config_name in {digest + ".json", "blobs/sha256/" + digest},
        "Saved config content does not match its actual archive digest",
    )
    after = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .}}", image_id],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    require(
        all(after[key] == inspection[key] for key in ("Id", "RootFS", "Config")),
        "Actual Engine image changed during config capture",
    )
    return raw


def adjudicate(report, inspection, runtime, profile, *, actual_config_raw, scan_source, now=None):
    """Match complete reviewed tuples and independently measured actual runtime bytes."""
    secrets = assess(report)
    require(secrets["passed"], "Every secret remains unresolved and blocks")
    require(
        isinstance(report.get("ReportID"), str) and report["ReportID"].strip(),
        "Complete original report identity required",
    )
    now = now or datetime.now(timezone.utc)
    created = datetime.fromisoformat(report["CreatedAt"].replace("Z", "+00:00"))
    require(created.tzinfo is not None, "Timestamp timezone required")
    require(
        now - timedelta(hours=24) <= created <= now + timedelta(minutes=5),
        "A fresh complete raw scan is required",
    )
    metadata = report["Metadata"]
    image_config = metadata["ImageConfig"]
    actual_config, config_digest = verify_actual_config(actual_config_raw, inspection)
    require(scan_source in {"docker", "archive"}, "Exact scan datasource required")
    expected_scan_config = copy.deepcopy(actual_config)
    if scan_source == "docker":
        # Trivy0.75.0 daemon configHistory uses time.Unix(h.Created, 0).UTC().
        for history in expected_scan_config.get("history", []):
            if "created" in history:
                value = datetime.fromisoformat(history["created"].replace("Z", "+00:00"))
                require(value.tzinfo is not None, "Actual history timestamp timezone required")
                history["created"] = (
                    value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                )
    expected_scan_id = inspection["Id"] if scan_source == "docker" else config_digest
    require(report["Trivy"]["Version"] == profile["scanner"], "Fixed reviewed scanner version required")
    require(
        inspection["Os"] == image_config["os"] == profile["os"]
        and inspection["Architecture"] == image_config["architecture"] == profile["architecture"],
        "Reviewed Linux/amd64 scope required",
    )
    require(
        metadata["ImageID"] == expected_scan_id
        and image_config == expected_scan_config
        and metadata["DiffIDs"] == inspection["RootFS"]["Layers"] == image_config["rootfs"]["diff_ids"],
        "Raw scan does not match the actual image/config/diffIDs",
    )
    config = {key: value for key, value in inspection["Config"].items() if key != "Labels"}
    scan_config = {key: value for key, value in image_config["config"].items() if key != "Labels"}
    require(config == scan_config == profile["runtime_config"], "Actual runtime configuration changed")
    require(
        report["SchemaVersion"] == 2 and isinstance(report["Results"], list), "Complete scan schema required"
    )
    results = report["Results"]
    require(
        {(result["Class"], result["Type"]) for result in results}
        == {("os-pkgs", "debian"), ("lang-pkgs", "python-pkg")},
        "Complete OS and Python package scan coverage required",
    )
    require(
        all(not result.get("Secrets") for result in results), "Every secret remains unresolved and blocks"
    )
    findings = [
        finding_tuple(result, finding) for result in results for finding in result.get("Vulnerabilities", [])
    ]
    packages = [package_tuple(result, package) for result in results for package in result["Packages"]]
    require(
        all(
            "DepsErrors" not in package
            or isinstance(package["DepsErrors"], list)
            and not package["DepsErrors"]
            for result in results
            for package in result["Packages"]
        ),
        "Incomplete package dependency metadata",
    )
    require(
        Counter(map(canonical, packages)) == Counter(map(canonical, profile["packages"])),
        "Complete scanned package inventory changed",
    )
    require(
        Counter(map(canonical, findings))
        == Counter(canonical(item["tuple"]) for item in profile["findings"]),
        "Unreviewed, changed or missing full-severity finding tuple",
    )
    require(runtime["uid"] == 10001, "Non-root isolated runtime probe required")
    require(
        runtime["native_elf_closure"] == profile["native_elf_closure"], "Reviewed whole ELF closure changed"
    )
    require(
        runtime["python_source_closure"] == profile["python_source_closure"],
        "Reviewed application/Python source closure changed",
    )
    require(
        runtime["official_glibc_items"] == len(profile["official_glibc_payload"])
        and not runtime["official_glibc_mismatches"],
        "Official fixed libc/gconv payload mismatch",
    )
    require(
        not runtime["locale_trees"] and runtime["lc_messages"] in {"C", "POSIX"},
        "Unfixed gettext issue requires renewed locale applicability review",
    )
    require(not runtime["bzip2recover"], "Affected bzip2recover component is present")
    sqlite = profile["sqlite"]
    require(
        runtime["sqlite"]["version"] == sqlite["version"]
        and runtime["sqlite"]["loaded"] == [{"path": sqlite["library"], "sha256": sqlite["sha256"]}],
        "Actual fixed SQLite library identity changed",
    )
    require(
        runtime["sqlite"]["extension_errors"]
        == ["no such table: zipfile", "no such function: zipfile", "not authorized"],
        "Reviewed unbuilt/disabled SQLite extension contract changed",
    )
    return {
        "passed": True,
        "review_profile": profile["name"],
        "review_profile_sha256": PROFILE_SHA256,
        "source_review_evidence_sha256": profile["source_review_evidence"]["sha256"],
        "risk_acceptance": False,
        "image_id": inspection["Id"],
        "portable_config_digest": config_digest,
        "scan_source": scan_source,
        "diff_ids": inspection["RootFS"]["Layers"],
        "raw_findings": len(findings),
        "unresolved_applicable_findings": 0,
        "raw_report_modified": False,
        "raw_zero_claim": False,
        "technical_dispositions": profile["findings"],
        "runtime_identity": runtime,
        "scope": "Exact reviewed application bytes/configuration and source contracts only; not all vulnerabilities.",
    }


PROBE_PURPOSE = "agent-exact-full-scope"


def cleanup_probe(name, image_id):
    found = subprocess.run(
        ["docker", "container", "inspect", name], text=True, capture_output=True, check=False
    )
    if found.returncode:
        if name in found.stderr and any(
            message in found.stderr for message in ("No such container:", "No such object:")
        ):
            return
        found.check_returncode()
    container = json.loads(found.stdout)[0]
    labels = container["Config"]["Labels"]
    require(
        container["Name"] == "/" + name
        and container["Image"] == image_id
        and container["Config"]["Image"] == image_id
        and labels.get("org.deepresearch.purpose") == PROBE_PURPOSE
        and labels.get("com.docker.compose.project") == name
        and container["HostConfig"]["NetworkMode"] == "none"
        and container["HostConfig"]["ReadonlyRootfs"] is True
        and not container["HostConfig"].get("Binds")
        and all(mount["Type"] == "tmpfs" and mount["Destination"] == "/tmp" for mount in container["Mounts"]),
        "Refusing cleanup: container is not the exact owned isolated probe",
    )
    subprocess.run(["docker", "rm", "-f", container["Id"]], capture_output=True, check=True)


def probe_image(image_id, profile):
    name = "dr-agent-fullscope-" + uuid.uuid4().hex[:12]
    command = [
        "docker",
        "run",
        "--name",
        name,
        "--label",
        "com.docker.compose.project=" + name,
        "--label",
        "org.deepresearch.purpose=" + PROBE_PURPOSE,
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--rm",
        "-i",
        "--entrypoint",
        "python",
        image_id,
        "-c",
        (ROOT / "scripts/agent_full_scope_probe.py").read_text(encoding="utf-8"),
    ]
    try:
        result = subprocess.run(
            command, input=json.dumps(profile), text=True, capture_output=True, timeout=180, check=True
        )
        return json.loads(result.stdout)
    finally:
        cleanup_probe(name, image_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-profile", required=True, choices=[PROFILE_NAME])
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scan-source", choices=["docker", "archive"], default="docker")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = load_profile(args.review_profile)
    inspection = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .}}", args.image],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    runtime = probe_image(inspection["Id"], profile)
    actual_config_raw = capture_actual_config(inspection)
    args.output.with_suffix(".image-config.raw.json").write_bytes(actual_config_raw)
    after = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .}}", inspection["Id"]],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    require(
        all(after[key] == inspection[key] for key in ("Id", "RootFS", "Config")),
        "Actual Engine image changed during runtime/config verification",
    )
    raw = args.report.read_bytes()
    verdict = adjudicate(
        json.loads(raw),
        inspection,
        runtime,
        profile,
        actual_config_raw=actual_config_raw,
        scan_source=args.scan_source,
    )
    verdict["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
