"""Keep new-install instructions and application-image defaults aligned."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_all_package_roles_keep_the_overlay_default_and_allow_override():
    overlay = (ROOT / "deployment/compose/compose.packages.yaml").read_text(encoding="utf-8")
    images = re.findall(r"^    image: (.+)$", overlay, re.MULTILINE)
    assert len(images) == 9
    assert images.count("ghcr.io/w205614/deepresearch-agent:${DEEPRESEARCH_PACKAGE_VERSION:-v0.1.4}") == 7
    assert "ghcr.io/w205614/deepresearch-backend:${DEEPRESEARCH_PACKAGE_VERSION:-v0.1.4}" in images
    assert "ghcr.io/w205614/deepresearch-web:${DEEPRESEARCH_PACKAGE_VERSION:-v0.1.4}" in images


def test_new_install_uses_exact_release_and_explicit_package_version():
    version = "v0.1.6"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = readme.split(f"## {version} 快速上手", 1)[1].split("\n## ", 1)[0]
    assert f"--branch {version}" in quickstart
    assert f"$env:DEEPRESEARCH_PACKAGE_VERSION = '{version}'" in quickstart
    assert "scripts/build_minio_alpine_20261121.py" in quickstart
    assert "scripts/fetch_approved_image.py" not in quickstart

    packages = (ROOT / "docs/container-packages.md").read_text(encoding="utf-8")
    install = packages.split("## 获取与启动", 1)[1].split("## 制品来源", 1)[0]
    assert f"../README.md#{version.replace('.', '')}-快速上手" in install
    assert f"DEEPRESEARCH_PACKAGE_VERSION={version}" in install

    source = (ROOT / "docs/source-release.md").read_text(encoding="utf-8")
    source_install = source.split("## 克隆后启动", 1)[1].split("\n## ", 1)[0]
    assert f"--branch {version}" in source_install
