"""Check the application release tag, documented startup and resolved images."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publish_application_packages import REPOSITORY, SERVICES, github, registry_manifest  # noqa: E402


def check_startup(root: Path, version: str) -> None:
    readme = (root / "README.md").read_text(encoding="utf-8")
    _, heading, section = readme.partition(f"\n## {version} 快速上手\n")
    if not heading:
        raise ValueError(f"README.md is missing the {version} quickstart heading")
    quickstart = section.split("\n## ", 1)[0]
    branches = re.findall(r"^\s*git clone [^\n]*--branch (\S+)", quickstart, re.MULTILINE)
    versions = re.findall(r"^\s*\$env:DEEPRESEARCH_PACKAGE_VERSION = '([^']+)'\s*$", quickstart, re.MULTILINE)
    if branches != [version] or versions != [version]:
        raise ValueError(f"README.md must check out {version} and explicitly select its application images")
    compose_files = re.search(r"^\s*\$composeFiles = @\(([^\n]+)\)\s*$", quickstart, re.MULTILINE)
    if not compose_files or not re.fullmatch(r"'-f', '[\w./-]+\.ya?ml'(?:, '-f', '[\w./-]+\.ya?ml')*",
                                               compose_files[1]):
        raise ValueError("README.md must list the quickstart Compose files in $composeFiles")
    compose_args = re.findall(r"'([^']+)'", compose_files[1])

    # Use the example configuration and synthetic credentials for rendering only.
    env = dict(os.environ)
    env.pop("DEEPRESEARCH_PACKAGE_VERSION", None)
    for key in (
        "OBJECT_STORE_SECRET_KEY", "POSTGRES_PASSWORD", "MIGRATOR_DB_PASSWORD", "JAVA_DB_PASSWORD",
        "PYTHON_DB_PASSWORD", "MONITOR_DB_PASSWORD", "KEYCLOAK_DB_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD",
        "MINIO_ROOT_PASSWORD", "MILVUS_STORAGE_PASSWORD", "GRAFANA_ADMIN_PASSWORD",
    ):
        env[key] = "release-check"
    roles = {"backend": "backend", "web": "web"}
    for role in ("agent", "worker", "document-worker", "migrate", "bootstrap-db", "ops-exporter", "alert-relay"):
        roles[role] = "agent"
    for mode, settings in (("default", env), ("explicit version", dict(env, DEEPRESEARCH_PACKAGE_VERSION=version))):
        rendered = subprocess.check_output(
            ["docker", "compose", "--env-file", ".env.example", "--profile", "*", *compose_args,
             "config", "--format", "json"], cwd=root, env=settings, text=True, encoding="utf-8",
        )
        services = json.loads(rendered)["services"]
        for role, image in roles.items():
            expected = f"ghcr.io/{REPOSITORY.split('/')[0].lower()}/deepresearch-{image}:{version}"
            actual = services.get(role, {}).get("image")
            if actual != expected:
                raise ValueError(f"Compose service {role} ({mode}) resolves to {actual!r}, expected {expected}")


def check_published_digests(version: str) -> None:
    release = github(f"/releases/tags/{version}", anonymous=True)
    for service in sorted(SERVICES):
        repository = f"ghcr.io/{REPOSITORY.split('/')[0].lower()}/deepresearch-{service}"
        references = set(re.findall(rf"{re.escape(repository)}@(sha256:[a-f0-9]{{64}})\b", release["body"]))
        actual = registry_manifest(repository, version, anonymous=True)
        if actual is None or references != {actual["digest"]}:
            raise ValueError(f"GitHub Release {version} must list the published Linux/amd64 digest for {repository}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Expected source tag and application image version")
    parser.add_argument("--published", action="store_true", help="Also verify the public Release and GHCR digests")
    args = parser.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.version):
        parser.error("Expected a version tag of the form vX.Y.Z")
    try:
        check_startup(ROOT, args.version)
        if args.published:
            check_published_digests(args.version)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release startup check failed: {exc}\n")
    print(f"{args.version}: README and Compose application images agree")
    if args.published:
        print(f"{args.version}: GitHub Release digests match the public Linux/amd64 images")


if __name__ == "__main__":
    main()
