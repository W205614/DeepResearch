"""Publish the exact verified application archives from an immutable artifact release."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPOSITORY = "W205614/DeepResearch"
SERVICES = {"agent", "backend", "web"}
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_image_id(path: Path, tag: str) -> str:
    """Read the config identity shared by classic and containerd Docker stores."""
    with tarfile.open(path) as archive:
        entries = json.load(archive.extractfile("manifest.json"))
        if len(entries) != 1 or tag not in entries[0].get("RepoTags", []):
            raise ValueError("Archive must contain only the accepted application tag")
        raw = archive.extractfile(entries[0]["Config"]).read()
    config = json.loads(raw)
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise ValueError("Archive platform differs from the accepted platform")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def validate_manifest(data: dict, source_sha: str, release_tag: str) -> None:
    if data.get("schema") != "deepresearch-application-packages-v1" or data.get("source_sha") != source_sha:
        raise ValueError("Publication manifest does not match the checked-out source")
    if data.get("repository") != REPOSITORY or data.get("artifact_release") != release_tag:
        raise ValueError("Publication manifest belongs to a different release or repository")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", data.get("version", "")):
        raise ValueError("An immutable application version is required")
    images = data.get("images", [])
    if len(images) != 3 or {item.get("service") for item in images} != SERVICES:
        raise ValueError("Exactly the three application images are required")
    if data.get("verification") != {"licenses": True, "security": True, "runtime": True}:
        raise ValueError("Application images have incomplete acceptance evidence")
    names = []
    for image in images:
        service = image["service"]
        if image.get("repository") != "ghcr.io/w205614/deepresearch-" + service:
            raise ValueError("Unexpected package namespace")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image.get("image_id", "")):
            raise ValueError("Invalid accepted image identity")
        if image.get("platform") != "linux/amd64":
            raise ValueError("Only the verified Linux/amd64 platform is supported")
        if image.get("archive_tag") != "dr-package-candidate-" + service + ":" + data["version"]:
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
    if not re.fullmatch(r"artifact-applications-v\d+\.\d+\.\d+-r\d+", args.release_tag):
        parser.error("An explicit application artifact release is required")
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
