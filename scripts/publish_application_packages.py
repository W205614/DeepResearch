"""Publish exact verified application archives from the corresponding version Release."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_image_secrets import assess  # noqa: E402

REPOSITORY = "W205614/DeepResearch"
SERVICES = {"agent", "backend", "web"}
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"
SEVERITIES = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_image_id(path: Path, tag: str) -> str:
    """Read the config identity shared by classic and containerd Docker stores."""
    with tarfile.open(path) as archive:
        entries = json.load(archive.extractfile("manifest.json"))
        if len(entries) != 1 or entries[0].get("RepoTags") != [tag]:
            raise ValueError("Archive must contain only the accepted application tag")
        raw = archive.extractfile(entries[0]["Config"]).read()
    config = json.loads(raw)
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise ValueError("Archive platform differs from the accepted platform")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def publication_scan_verdict(scan: dict, image: dict, diff_ids: list[str], started: datetime, finished: datetime) -> dict:
    secrets = assess(scan)
    metadata = scan["Metadata"]
    created = datetime.fromisoformat(scan["CreatedAt"].replace("Z", "+00:00"))
    if (not scan.get("ReportID") or created.tzinfo is None or not started <= created <= finished
            or metadata.get("ImageID") != image["image_id"] or metadata.get("DiffIDs") != diff_ids
            or metadata.get("ImageConfig", {}).get("os") != "linux"
            or metadata.get("ImageConfig", {}).get("architecture") != "amd64"):
        raise ValueError("Fresh image scan does not match the verified loaded archive")
    required = {"agent": {("os-pkgs", "debian"), ("lang-pkgs", "python-pkg")},
                "backend": {("os-pkgs", "alpine"), ("lang-pkgs", "jar")},
                "web": {("os-pkgs", "alpine")}}[image["service"]]
    covered = set()
    unresolved, counts = [], Counter()
    for result in scan["Results"]:
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            raise ValueError("Malformed image vulnerability inventory")
        if result.get("Class") in {"os-pkgs", "lang-pkgs"}:
            packages = result.get("Packages")
            if not isinstance(packages, list) or not packages:
                raise ValueError("Full image package inventory is required")
            covered.add((result["Class"], result.get("Type")))
        else:
            packages = []
        inventory = {(p["Name"], (str(p["Epoch"]) + ":" if p.get("Epoch") else "") + p["Version"]
                      + ("-" + p["Release"] if p.get("Release") else "")) for p in packages}
        for finding in vulnerabilities:
            if (not isinstance(finding, dict) or finding.get("Severity") not in SEVERITIES
                    or not finding.get("VulnerabilityID")
                    or (finding.get("PkgName"), finding.get("InstalledVersion")) not in inventory):
                raise ValueError("Image vulnerability has no complete severity and package identity")
            counts[finding["Severity"]] += 1
            unresolved.append({key: finding[key] for key in (
                "VulnerabilityID", "PkgName", "InstalledVersion", "Severity")})
    if not required <= covered:
        raise ValueError("Required application OS or language-package scan is missing")
    # This strict default intentionally does not adjudicate even known fixed-code
    # false positives. A green legacy HIGH/CRITICAL gate cannot waive other rows.
    return {"passed": not unresolved and secrets["passed"],
            "policy": "Every raw vulnerability at every severity and every secret blocks publication",
            "raw_vulnerability_counts": dict(counts), "unresolved_vulnerabilities": unresolved,
            "unresolved_secret_count": secrets["secret_findings"],
            "secret_severity_counts": secrets["secret_severity_counts"],
            "applicability_exceptions_applied": [], "raw_report_modified": False}


def scan_loaded_archive(archive: Path, image: dict, output: Path, *, scanner: str = "trivy", cache_dir: Path | None = None) -> None:
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    receipt = {"passed": False, "service": image["service"], "image_id": image["image_id"],
               "loaded_archive_sha256": file_hash(archive), "raw_report_modified": False}
    raw = output / "raw.private.json"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TRIVY_")}
    config, ignore = output / "empty-trivy.yaml", output / "empty.ignore"
    config.write_bytes(b"{}\n")
    ignore.write_bytes(b"")
    try:
        with tarfile.open(archive) as saved:
            entry, = json.load(saved.extractfile("manifest.json"))
            diff_ids = json.load(saved.extractfile(entry["Config"]))["rootfs"]["diff_ids"]
        version = subprocess.run([scanner, "--version"], capture_output=True, env=environment,
                                 timeout=30, check=True)
        (output / "scanner-version.log").write_bytes(version.stdout)
        if not re.search(rb"^Version:\s*0\.75\.0\s*$", version.stdout, re.MULTILINE):
            raise ValueError("Publication requires the fixed Trivy 0.75.0 scanner")
        command = [scanner, "image", "--input", str(archive.resolve()), "--config", str(config),
                   "--ignorefile", str(ignore), "--scanners", "vuln,secret", "--list-all-pkgs",
                   "--severity", ",".join(SEVERITIES), "--ignore-unfixed=false", "--format", "json",
                   "--output", str(raw), "--exit-code", "0", "--timeout", "10m"]
        if cache_dir is not None:
            command += ['--cache-dir', str(cache_dir.resolve())]
        started = datetime.now(timezone.utc)
        receipt.update(started_at=started.isoformat(), scanner_version="0.75.0", command=command)
        try:
            process = subprocess.run(command, cwd=output, capture_output=True, env=environment, timeout=630)
        except subprocess.TimeoutExpired as error:
            (output / "stdout.private.log").write_bytes(error.stdout or b"")
            (output / "stderr.private.log").write_bytes(error.stderr or b"")
            raise ValueError("Publication image scan timed out; partial reports cannot authorize a push") from None
        finished = datetime.now(timezone.utc)
        (output / "stdout.private.log").write_bytes(process.stdout)
        (output / "stderr.private.log").write_bytes(process.stderr)
        receipt.update(finished_at=finished.isoformat(), scanner_exit_code=process.returncode)
        if process.returncode != 0:
            raise ValueError("Publication image scanner failed; raw logs retained")
        if (not raw.is_file() or raw.is_symlink() or not 0 < raw.stat().st_size <= 64 * 1024**2
                or config.read_bytes() != b"{}\n" or ignore.read_bytes() != b""):
            raise ValueError("Complete unfiltered publication image scan is required")
        receipt["raw_sha256"] = file_hash(raw)
        verdict = publication_scan_verdict(json.loads(raw.read_bytes()), image, diff_ids, started, finished)
        if image.get('security_review_profile'):
            if image['service'] != 'agent' or image['security_review_profile'] != 'agent-full-scope-20261110':
                raise ValueError('Unknown application-specific technical review')
            from scripts import check_agent_full_scope

            profile = check_agent_full_scope.load_profile(image['security_review_profile'])
            inspection, = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image['archive_tag']]))
            actual_config_raw = check_agent_full_scope.capture_actual_config(inspection)
            if 'sha256:' + hashlib.sha256(actual_config_raw).hexdigest() != image['image_id']:
                raise ValueError('Actual reviewed runtime portable config differs from the accepted archive')
            runtime = check_agent_full_scope.probe_image(inspection['Id'], profile)
            technical = check_agent_full_scope.adjudicate(json.loads(raw.read_bytes()), inspection, runtime,
                profile, actual_config_raw=actual_config_raw, scan_source='archive')
            verdict = {**verdict, 'strict_default_verdict': verdict,
                'passed': technical['passed'], 'unresolved_vulnerabilities': [],
                'applicability_exceptions_applied': technical['technical_dispositions'],
                'technical_review': technical,
                'policy': 'Exact all-severity fixed-code and application-path review; every new finding or changed runtime blocks'}
        receipt.update(verdict)
        # A raw secret match can contain credential values. Keep it and scanner
        # logs in the runner's private output, outside the uploaded artifact set.
        if not verdict["unresolved_secret_count"]:
            raw = raw.rename(output / "raw.json")
        receipt["raw_file"] = raw.name
        if not verdict["passed"]:
            raise ValueError("Publication blocked by unresolved all-severity image findings; see verdict.json")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        receipt.update(passed=False, error_type=type(error).__name__)
        raise
    finally:
        (output / "verdict.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def validate_manifest(data: dict, source_sha: str, release_tag: str) -> None:
    if data.get("schema") != "deepresearch-application-packages-v1" or data.get("source_sha") != source_sha:
        raise ValueError("Publication manifest does not match the checked-out source")
    if data.get("repository") != REPOSITORY or data.get("artifact_release") != release_tag:
        raise ValueError("Publication manifest belongs to a different release or repository")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", data.get("version", "")):
        raise ValueError("An immutable application version is required")
    if (release_tag != data['version']
            and not re.fullmatch('artifact-applications-' + re.escape(data['version']) + r'-r\d+', release_tag)):
        raise ValueError("Release tag and immutable application version differ")
    images = data.get("images", [])
    if len(images) != 3 or {item.get("service") for item in images} != SERVICES:
        raise ValueError("Exactly the three application images are required")
    if data.get("verification") != {"licenses": True, "security": True, "runtime": True}:
        raise ValueError("Application images have incomplete acceptance evidence")
    names = []
    for image in images:
        service = image["service"]
        if ('security_review_profile' in image and (service != 'agent'
                or image['security_review_profile'] != 'agent-full-scope-20261110')):
            raise ValueError('Only the exact Agent technical review can replace the raw-zero default')
        if image.get("repository") != "ghcr.io/w205614/deepresearch-" + service:
            raise ValueError("Unexpected package namespace")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image.get("image_id", "")):
            raise ValueError("Invalid accepted image identity")
        if image.get("platform") != "linux/amd64":
            raise ValueError("Only the verified Linux/amd64 platform is supported")
        archive_tag = "dr-package-candidate-" + service + ":" + data["version"]
        if release_tag == data["version"]:
            archive_tag += "-" + source_sha[:12]
        if image.get("archive_tag") != archive_tag:
            raise ValueError("Invalid archive-local image tag")
    for archive in [*images, data.get("sources", {})]:
        if not re.fullmatch(r"[a-f0-9]{64}", archive.get("archive_sha256", "")) or not archive.get("parts"):
            raise ValueError("Archive identity and parts are required")
        for part in archive["parts"]:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+", part.get("name", "")):
                raise ValueError("Invalid archive part name")
            if not re.fullmatch(r"[a-f0-9]{64}", part.get("sha256", "")):
                raise ValueError("Invalid archive part digest")
            if type(part.get("bytes")) is not int or not 0 < part["bytes"] <= 64 * 1024**2:
                raise ValueError("Archive part exceeds its documented bound")
            names.append(part["name"])
    if len(names) != len(set(names)):
        raise ValueError("Archive parts must have unique names")


def github(path: str) -> dict:
    request = Request("https://api.github.com/repos/" + REPOSITORY + path,
                      headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                               "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def download(asset: dict, destination: Path) -> None:
    prefix = "https://github.com/" + REPOSITORY + "/releases/download/"
    if not asset["browser_download_url"].startswith(prefix):
        raise ValueError("Unexpected public artifact download host or repository")
    # Public downloads receive no GitHub credential. Retries are limited to the
    # observed transport failures; integrity failure always stops publication.
    subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                    "--connect-timeout", "20", "--max-time", "180", "--retry", "2",
                    "--retry-all-errors", "--retry-max-time", "180", "--output", str(destination),
                    asset["browser_download_url"]], check=True)


def assemble(spec: dict, assets: dict, directory: Path, name: str) -> Path:
    destination = directory / (name + ".tar.gz")
    with destination.open("xb") as output:
        for part in spec["parts"]:
            asset = assets[part["name"]]
            if asset["size"] != part["bytes"]:
                raise ValueError("Published archive part size differs from the accepted manifest")
            path = directory / part["name"]
            download(asset, path)
            if path.stat().st_size != part["bytes"] or file_hash(path) != part["sha256"]:
                raise ValueError("Published archive part bytes differ from the accepted manifest")
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    output.write(block)
    if file_hash(destination) != spec["archive_sha256"]:
        raise ValueError("Reassembled archive differs from the accepted artifact")
    return destination


def registry_manifest(repository: str, version: str) -> dict | None:
    name = repository.removeprefix("ghcr.io/")
    auth = base64.b64encode((os.environ["GITHUB_ACTOR"] + ":" + os.environ["GITHUB_TOKEN"]).encode()).decode()
    query = urlencode({"service": "ghcr.io", "scope": "repository:" + name + ":pull,push"})
    with urlopen(Request("https://ghcr.io/token?" + query, headers={"Authorization": "Basic " + auth}), timeout=45) as response:
        token = json.load(response)["token"]
    request = Request("https://ghcr.io/v2/" + name + "/manifests/" + version,
                      headers={"Authorization": "Bearer " + token, "Accept": MANIFEST_ACCEPT})
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read()
            manifest = json.loads(raw)
            digest = response.headers["Docker-Content-Digest"]
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    if digest != "sha256:" + hashlib.sha256(raw).hexdigest() or "config" not in manifest:
        raise ValueError("Registry did not return a verified single-platform image manifest")
    return {"digest": digest, "image_id": manifest["config"]["digest"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, default=Path(".cache/eval/application-publication"))
    args = parser.parse_args()
    if not re.fullmatch(r"(?:v\d+\.\d+\.\d+|artifact-applications-v\d+\.\d+\.\d+-r\d+)", args.release_tag):
        parser.error("An explicit version Release or retained historical application artifact release is required")
    if not re.fullmatch(r"[a-f0-9]{64}", args.manifest_sha256):
        parser.error("An exact accepted manifest SHA-256 is required")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    runs = github("/actions/runs?" + urlencode({"head_sha": sha, "per_page": 100}))["workflow_runs"]
    for name in ["CI", "Security"]:
        matches = [run for run in runs if run["name"] == name and run["event"] == "push"]
        if not matches or matches[0]["status"] != "completed" or matches[0]["conclusion"] != "success":
            raise ValueError("Final-source " + name + " must succeed before package publication")
    release = github("/releases/tags/" + args.release_tag)
    if release["draft"] or not release["published_at"] or release["target_commitish"] != sha:
        raise ValueError("Artifact release is not published for the exact final source")
    if github('/commits/' + args.release_tag)['sha'] != sha:
        raise ValueError("The actual published tag resolves to another source commit")
    assets = {asset["name"]: asset for asset in release["assets"]}
    if assets["publication-manifest.json"]["size"] > 1024 * 1024:
        raise ValueError("Publication manifest exceeds its size bound")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_file = args.output / "publication-manifest.json"
    download(assets["publication-manifest.json"], manifest_file)
    if file_hash(manifest_file) != args.manifest_sha256:
        raise ValueError("Publication manifest differs from the explicit dispatch input")
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    validate_manifest(data, sha, args.release_tag)
    assemble(data["sources"], assets, args.output, "corresponding-sources")
    existing = {}
    for image in data["images"]:
        archive = assemble(image, assets, args.output, image["service"])
        if archive_image_id(archive, image["archive_tag"]) != image["image_id"]:
            raise ValueError("Archive config differs from the accepted artifact")
        subprocess.run(["docker", "load", "--input", str(archive)], check=True)
        # Docker 29's containerd store may expose an index as inspect.Id. Verify
        # the loaded bytes through Docker's portable archive config instead.
        loaded = args.output / (image["service"] + "-loaded.tar")
        subprocess.run(["docker", "image", "save", "--output", str(loaded), image["archive_tag"]], check=True)
        if archive_image_id(loaded, image["archive_tag"]) != image["image_id"]:
            raise ValueError("Loaded image differs from the accepted artifact")
        scan_loaded_archive(loaded, image, args.output / "security" / image["service"])
        current = registry_manifest(image["repository"], data["version"])
        if current and current["image_id"] != image["image_id"]:
            raise ValueError("An existing immutable package version has different bytes; do not overwrite")
        existing[image["service"]] = current
    subprocess.run(["docker", "login", "ghcr.io", "--username", os.environ["GITHUB_ACTOR"], "--password-stdin"],
                   input=os.environ["GITHUB_TOKEN"], text=True, check=True)
    published = []
    for image in data["images"]:
        target = image["repository"] + ":" + data["version"]
        if not existing[image["service"]]:
            subprocess.run(["docker", "tag", image["archive_tag"], target], check=True)
            subprocess.run(["docker", "push", target], check=True)
        actual = registry_manifest(image["repository"], data["version"])
        if not actual or actual["image_id"] != image["image_id"]:
            raise ValueError("Published package config does not match the accepted local image")
        published.append({"service": image["service"], "reference": target, "image_id": image["image_id"],
                          "digest": actual["digest"], "immutable_reference": image["repository"] + "@" + actual["digest"]})
        (args.output / "publication.json").write_text(json.dumps({"source_sha": sha, "version": data["version"],
            "artifact_release": args.release_tag, "manifest_sha256": args.manifest_sha256, "images": published}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_sha": sha, "images": published, "visibility": "Verify or set package visibility separately; no anonymous-access claim yet"}))


if __name__ == "__main__":
    main()
