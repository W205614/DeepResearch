"""Keep new-install instructions and application-image defaults aligned."""
import json
from pathlib import Path
import re

import pytest

from scripts import check_release_startup

ROOT = Path(__file__).resolve().parents[1]


def documented_version():
    packages = (ROOT / "docs/container-packages.md").read_text(encoding="utf-8")
    heading = re.search(r"^## (v\d+\.\d+\.\d+) 启动入口$", packages, re.MULTILINE)
    assert heading is not None, "Container guide must identify the startup version"
    return heading.group(1)


def test_all_package_roles_keep_the_overlay_default_and_allow_override():
    overlay = (ROOT / "deployment/compose/compose.packages.yaml").read_text(encoding="utf-8")
    images = re.findall(r"^    image: (.+)$", overlay, re.MULTILINE)
    assert len(images) == 9
    reference = "ghcr.io/w205614/deepresearch-{}:${{DEEPRESEARCH_PACKAGE_VERSION:-" + documented_version() + "}}"
    assert images.count(reference.format("agent")) == 7
    assert reference.format("backend") in images
    assert reference.format("web") in images


def test_new_install_uses_exact_release_and_explicit_package_version():
    packages = (ROOT / "docs/container-packages.md").read_text(encoding="utf-8")
    version = documented_version()

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart_heading = f"## {version} 快速上手\n"
    assert quickstart_heading in readme
    quickstart = readme.split(quickstart_heading, 1)[1].split("\n## ", 1)[0]
    assert f"--branch {version}" in quickstart
    assert f"$env:DEEPRESEARCH_PACKAGE_VERSION = '{version}'" in quickstart
    assert "scripts/build_minio_alpine_20261121.py" in quickstart
    assert "scripts/fetch_approved_image.py" not in quickstart

    install = packages.split("## 获取与启动", 1)[1].split("## 制品来源", 1)[0]
    assert f"../README.md#{version.replace('.', '')}-快速上手" in install
    assert f"DEEPRESEARCH_PACKAGE_VERSION={version}" in install

    source = (ROOT / "docs/source-release.md").read_text(encoding="utf-8")
    assert f"[`{version}`](https://github.com/W205614/DeepResearch/releases/tag/{version})" in source
    source_install = source.split("## 克隆后启动", 1)[1].split("\n## ", 1)[0]
    assert f"--branch {version}" in source_install


@pytest.mark.parametrize("mismatch", [None, "heading", "branch", "version", "worker-image", "worker-default"])
def test_release_check_rejects_stale_startup_references(tmp_path, monkeypatch, mismatch):
    version, old_version = "v1.2.3", "v1.2.2"
    heading = old_version if mismatch == "heading" else version
    branch = old_version if mismatch == "branch" else version
    package_version = old_version if mismatch == "version" else version
    readme = f"""# DeepResearch

## {heading} 快速上手

```powershell
git clone --branch {branch} https://github.com/W205614/DeepResearch.git
$env:DEEPRESEARCH_PACKAGE_VERSION = '{package_version}'
$composeFiles = @('-f', 'compose.yaml', '-f', 'deployment/compose/compose.packages.yaml')
```
"""
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")

    def render_compose(args, **kwargs):
        assert mismatch not in {"heading", "branch", "version"}
        assert args[args.index("-f"):] == ["-f", "compose.yaml", "-f", "deployment/compose/compose.packages.yaml",
                                           "config", "--format", "json"]
        explicit = kwargs["env"].get("DEEPRESEARCH_PACKAGE_VERSION")
        assert explicit in {None, version}
        services = dict.fromkeys(("agent", "migrate", "bootstrap-db", "document-worker", "alert-relay", "ops-exporter"),
                                 {"image": f"ghcr.io/w205614/deepresearch-agent:{version}"})
        stale_worker = (mismatch == "worker-image" and explicit is not None
                        or mismatch == "worker-default" and explicit is None)
        worker_version = old_version if stale_worker else version
        services["worker"] = {"image": f"ghcr.io/w205614/deepresearch-agent:{worker_version}"}
        services["backend"] = {"image": f"ghcr.io/w205614/deepresearch-backend:{version}"}
        services["web"] = {"image": f"ghcr.io/w205614/deepresearch-web:{version}"}
        return json.dumps({"services": services})

    monkeypatch.setattr(check_release_startup.subprocess, "check_output", render_compose)
    if mismatch:
        with pytest.raises(ValueError, match="Compose service worker" if mismatch.startswith("worker") else "README.md"):
            check_release_startup.check_startup(tmp_path, version)
    else:
        check_release_startup.check_startup(tmp_path, version)
