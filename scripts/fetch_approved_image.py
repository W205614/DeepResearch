"""Fetch and load an independently approved exact OCI artifact; never rebuild it."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.image_disposition import (  # noqa: E402
    DispositionError,
    canonical,
    MAX_ARCHIVE_BYTES,
    POLICY,
    file_hash,
    load_binding,
    verify_archive,
)

REPOSITORY = "W205614/DeepResearch"
SOURCE_TAG = "dr-opt-minio-go1273-compat:20261026"
APPROVED_TAG = "deepresearch-approved-minio:20261026"
SCAN_TAG = "deepresearch-scan-minio:ci"


def artifact_spec(artifact):
    if artifact == "minio":
        return {"policy": POLICY, "source_tag": SOURCE_TAG, "approved_tag": APPROVED_TAG, "scan_tag": SCAN_TAG,
                "asset_id": 678114320, "asset_name": "approved-minio-151cd83d.oci.tar", "archive_cap": MAX_ARCHIVE_BYTES,
                "release_id": 424228449, "release_tag": "artifact-minio-fixed-20261026",
                "storage_hosts": frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})}
    if artifact == "grafana":
        return {"policy": Path("deployment/security/grafana-reviewed-disposition.json"),
                "source_tag": "dr-opt-grafana-musl-fixed:20261026", "approved_tag": "deepresearch-approved-grafana:20261026",
                "scan_tag": "deepresearch-scan-grafana:ci", "asset_id": 678426319, "asset_name": "grafana-9a68d3a7.oci.tar",
                "release_id": 424356978, "release_tag": "artifact-grafana-reviewed-20261026",
                "archive_cap": 2 * 1024**3,
                "storage_hosts": frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})}
    raise DispositionError("Unknown approved-artifact namespace")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


PART_BYTES = 64 * 1024 * 1024
MAX_PARTS = 32


def verify_release_publication(transport, artifact, *, allow_unpublished_verification=False):
    spec = artifact_spec(artifact)
    if (transport.get("repository") != REPOSITORY
            or type(transport.get("release_id")) is not int or transport["release_id"] != spec["release_id"]
            or transport.get("release_tag", transport.get("tag")) != spec["release_tag"]):
        raise DispositionError("Artifact release identity differs from its fixed repository/release")
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/{spec['release_id']}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN", ""):
        headers["Authorization"] = "Bearer " + token
    try:
        # Release metadata must come directly from the fixed official REST endpoint.
        with build_opener(NoRedirect).open(Request(url, headers=headers), timeout=60) as response:
            raw = response.read(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2:
            raise DispositionError("Release metadata exceeds its bound")
        release = json.loads(raw)
    except HTTPError as error:
        raise DispositionError(f"Release publication could not be verified (HTTP {error.code})") from None
    if (type(release.get("id")) is not int or release["id"] != spec["release_id"]
            or release.get("tag_name") != spec["release_tag"] or type(release.get("draft")) is not bool):
        raise DispositionError("Live release metadata has a different identity")
    published = release.get("published_at")
    if published is not None:
        if not isinstance(published, str) or not published:
            raise DispositionError("Release publication timestamp is invalid")
        try:
            timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError
        except ValueError:
            raise DispositionError("Release publication timestamp is invalid") from None
    verified = release["draft"] is False and published is not None
    if not verified and not allow_unpublished_verification:
        raise DispositionError("Release is not published; cached content cannot bypass publication")
    return {"release_id": spec["release_id"], "release_tag": spec["release_tag"],
            "draft": release["draft"], "published_at": published, "published_verified_live": verified,
            "unpublished_verification_only": bool(allow_unpublished_verification)}


def open_asset_response(asset_id, storage_hosts):
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset_id}"
    headers = {"Accept": "application/octet-stream", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = "Bearer " + token
    opener = build_opener(NoRedirect)
    try:
        response = opener.open(Request(url, headers=headers), timeout=60)
    except HTTPError as error:
        if error.code != 302:
            raise DispositionError(
                f"Approved repository artifact is unavailable (HTTP {error.code})"
            ) from None
        redirected = error.headers.get("Location", "")
        parsed = urlsplit(redirected)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.hostname
            not in storage_hosts
        ):
            raise DispositionError("Unrecognized GitHub artifact storage redirect") from None
        # Signed storage URL receives no GitHub authorization header.
        response = opener.open(Request(redirected), timeout=120)
    return response


def validate_parts(binding, transport):
    spec = artifact_spec("grafana")
    total = binding["archive_bytes"]
    if (transport.get("artifact") != "grafana" or transport.get("repository") != REPOSITORY
            or transport.get("transport_mode") != "github_asset_parts"
            or transport.get("release_id") != 424356978 or transport.get("release_tag") != "artifact-grafana-reviewed-20261026"
            or transport.get("asset_name") != spec["asset_name"] or transport.get("asset_sha256") != binding["archive_sha256"]
            or type(transport.get("archive_bytes")) is not int or transport["archive_bytes"] != total
            or transport.get("part_bytes") != PART_BYTES or total > spec["archive_cap"]
            or transport.get("asset_id") is not None or transport.get("api_url") is not None
            or transport.get("whole_archive_server_digest") is not None or transport.get("remote_digest") is not None):
        raise DispositionError("Grafana parts transport has a different subject/repository/scope")
    parts = transport.get("parts")
    expected_count = (total + PART_BYTES - 1) // PART_BYTES
    if not isinstance(parts, list) or not 0 < len(parts) <= MAX_PARTS or len(parts) != expected_count:
        raise DispositionError("Grafana parts inventory is missing or incomplete")
    seen = set()
    for i, part in enumerate(parts):
        size = min(PART_BYTES, total - i * PART_BYTES)
        asset_id, sha = part.get("asset_id"), part.get("sha256")
        if (type(part.get("index")) is not int or part["index"] != i
                or type(asset_id) is not int or asset_id <= 0 or asset_id in seen
                or part.get("asset_name") != spec["asset_name"] + f".part-{i:03d}"
                or part.get("api_url") != f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset_id}"
                or type(part.get("bytes")) is not int or part["bytes"] != size or not 0 < size <= PART_BYTES
                or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
                or part.get("remote_digest") != "sha256:" + sha or part.get("remote_state") != "uploaded"):
            raise DispositionError("Every ordered Grafana part needs exact name/size/server digest and uploaded state")
        seen.add(asset_id)
    if sum(p["bytes"] for p in parts) != total:
        raise DispositionError("Grafana parts total length differs")
    return parts


def download_parts(destination, binding, transport, *, allow_unpublished_verification=False, publication_receipt=None):
    parts = validate_parts(binding, transport)
    publication = verify_release_publication(transport, "grafana", allow_unpublished_verification=allow_unpublished_verification)
    if publication_receipt is not None:
        publication_receipt.update(publication)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size == binding["archive_bytes"] and file_hash(destination) == binding["archive_sha256"]:
            return destination
        raise DispositionError("Existing assembled archive differs; preserve it and investigate")
    cache = destination.parent / ("grafana-parts-" + binding["archive_sha256"][:16])
    cache.mkdir(parents=True, exist_ok=True)
    verified = []
    for part in parts:
        target = cache / (f"{part['index']:03d}-" + part["sha256"] + ".part")
        if target.exists():
            if target.is_symlink() or target.stat().st_size != part["bytes"] or file_hash(target) != part["sha256"]:
                raise DispositionError("Cached Grafana part changed; preserve it and investigate")
            verified.append(target)
            continue
        response = open_asset_response(part["asset_id"], artifact_spec("grafana")["storage_hosts"])
        with response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) != part["bytes"]:
                raise DispositionError("Grafana part declared length differs")
            with tempfile.NamedTemporaryFile(prefix=f"grafana-{part['index']:03d}-", suffix=".partial", dir=cache, delete=False) as output:
                temporary, received, hashed = Path(output.name), 0, hashlib.sha256()
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > part["bytes"]:
                        raise DispositionError("Grafana part exceeds its bound")
                    hashed.update(block)
                    output.write(block)
        if received != part["bytes"] or hashed.hexdigest() != part["sha256"]:
            raise DispositionError("Grafana part incomplete/changed; partial retained")
        temporary.rename(target)
        verified.append(target)
    with tempfile.NamedTemporaryFile(prefix="grafana-assembled-", suffix=".partial", dir=destination.parent, delete=False) as output:
        assembled, size, hashed = Path(output.name), 0, hashlib.sha256()
        for path in verified:
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > binding["archive_bytes"]:
                        raise DispositionError("Grafana assembled archive exceeds its bound")
                    hashed.update(block)
                    output.write(block)
    if size != binding["archive_bytes"] or hashed.hexdigest() != binding["archive_sha256"]:
        raise DispositionError("Grafana assembled archive whole SHA/size differs; partial retained")
    assembled.rename(destination)
    return destination


def download_archive(destination, binding, transport, *, artifact="minio", allow_unpublished_verification=False, publication_receipt=None):
    spec = artifact_spec(artifact)
    if artifact == "grafana" and transport.get("transport_mode") == "github_asset_parts":
        return download_parts(destination, binding, transport, allow_unpublished_verification=allow_unpublished_verification,
                              publication_receipt=publication_receipt)
    if transport.get("transport_mode", "github_asset_single") != "github_asset_single":
        raise DispositionError("Unknown artifact transport mode")
    if (transport.get("repository") != REPOSITORY or type(transport.get("asset_id")) is not int
            or transport.get("asset_id") != spec["asset_id"] or transport.get("asset_name") != spec["asset_name"]
            or transport.get("asset_sha256") != binding["archive_sha256"]
            or transport.get("artifact", "minio") != artifact):
        raise DispositionError("Approved archive has no bound repository asset")
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{transport['asset_id']}"
    if transport.get("api_url") != url:
        raise DispositionError("Artifact API address differs from the expected repository")
    if artifact == "grafana" and transport.get("remote_digest") != "sha256:" + binding["archive_sha256"]:
        raise DispositionError("Grafana upload/remote digest has not been verified")
    publication = verify_release_publication(transport, artifact, allow_unpublished_verification=allow_unpublished_verification)
    if publication_receipt is not None:
        publication_receipt.update(publication)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_hash(destination) == binding["archive_sha256"]:
            return destination
        raise DispositionError("Existing archive differs; preserve it and investigate")
    response = open_asset_response(transport["asset_id"], spec["storage_hosts"])
    with response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) != binding["archive_bytes"]:
            raise DispositionError("Downloaded artifact length differs")
        with tempfile.NamedTemporaryFile(
            prefix=artifact + "-approved-", suffix=".partial", dir=destination.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            received = 0
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > min(binding["archive_bytes"], spec["archive_cap"]):
                    raise DispositionError("Downloaded artifact exceeds its bound")
                output.write(block)
    if received != binding["archive_bytes"] or file_hash(temporary) != binding["archive_sha256"]:
        raise DispositionError("Downloaded artifact is incomplete or changed; partial file retained")
    temporary.rename(destination)
    return destination


def inspect(reference):
    result = subprocess.run(["docker", "image", "inspect", reference], capture_output=True, check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def exact_image(inspection, binding):
    return bool(
        inspection
        and inspection.get("Id")
        in {binding["index_digest"], binding["platform_manifest_digest"], binding["config_digest"]}
        and inspection.get("RootFS", {}).get("Layers") == binding["diff_ids"]
        and inspection.get("Os") == "linux"
        and inspection.get("Architecture") == "amd64"
    )


def load_image(archive, binding, tag, *, artifact="minio"):
    spec = artifact_spec(artifact)
    if tag not in {spec["approved_tag"], spec["scan_tag"]}:
        raise DispositionError("Artifact output tag belongs to another namespace")
    references = [binding["index_digest"], binding["config_digest"], spec["source_tag"]]
    selected = next((reference for reference in references if exact_image(inspect(reference), binding)), None)
    if selected is None:
        result = subprocess.run(["docker", "load", "--input", str(archive)], capture_output=True, check=False)
        if result.returncode:
            raise DispositionError("Docker could not load the verified OCI archive")
        selected = next(
            (reference for reference in references if exact_image(inspect(reference), binding)), None
        )
    if selected is None:
        raise DispositionError("Loaded image config/filesystem does not match the approved artifact")
    if subprocess.run(["docker", "tag", selected, tag], capture_output=True, check=False).returncode:
        raise DispositionError("Could not label the verified image")
    actual = inspect(tag)
    if not exact_image(actual, binding):
        raise DispositionError("The resulting image is not the approved config/filesystem")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--artifact", choices=["minio", "grafana"], default="minio")
    parser.add_argument("--tag")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.output = args.output or ROOT / (".cache/eval/approved-image-load.json" if args.artifact == "minio" else ".cache/eval/approved-grafana-load.json")
    try:
        spec = artifact_spec(args.artifact)
        tag = args.tag or spec["approved_tag"]
        if tag not in {spec["approved_tag"], spec["scan_tag"]}:
            raise DispositionError("Artifact output tag belongs to another namespace")
        if args.artifact == "grafana":
            from scripts import grafana_image_disposition as grafana
            binding = grafana.load_binding()
        else:
            binding = load_binding()
        policy = json.loads((ROOT / spec["policy"]).read_text(encoding="utf-8"))
        archive = args.archive
        publication = {}
        if archive is None:
            destination = ROOT / (".cache/eval/approved-" + args.artifact + ".oci.tar")
            if args.artifact == "minio":
                archive = download_archive(destination, binding, policy["transport"], publication_receipt=publication)
            else:
                archive = download_archive(destination, binding, policy["transport"], artifact=args.artifact, publication_receipt=publication)
        verified = grafana.verify_archive(archive, binding) if args.artifact == "grafana" else verify_archive(archive, binding)
        inspection = load_image(archive, binding, tag, artifact=args.artifact)
        verified.pop("authorization_evaluated", None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        inspect_path = args.output.with_name(args.output.stem + "-inspect.json")
        inspect_path.write_text(json.dumps(inspection, indent=2), encoding="utf-8")
        result = {
            "passed": True,
            "mode": "verified_load_only_scan_gate_still_required",
            "archive": str(archive),
            "tag": tag,
            "artifact": args.artifact,
            "binding_sha256": hashlib.sha256(canonical(binding)).hexdigest(),
            "policy_authorization_verified": True,
            "publication": publication if not args.archive else {"mode": "explicit_local_archive_no_remote_publication_claim"},
            "archive_source": "explicit_local" if args.archive else "fixed_repository_asset_parts" if policy["transport"].get("transport_mode") == "github_asset_parts" else "fixed_repository_asset",
            "transport_mode": policy["transport"].get("transport_mode", "github_asset_single"),
            "inspection": str(inspect_path),
            **verified,
        }
    except Exception as error:
        # In particular, never print a signed storage URL or credential on failure.
        result = {"passed": False, "mode": "failed_closed", "error_type": type(error).__name__}
        if isinstance(error, DispositionError):
            result["error"] = str(error)
        args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({key: result[key] for key in ("passed", "mode", "error_type", "error") if key in result})
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
