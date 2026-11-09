import copy

import pytest

from scripts.publish_application_packages import validate_manifest


def manifest():
    def archive(name):
        return {"archive_sha256": "a" * 64,
                "parts": [{"name": name + ".part000", "sha256": "b" * 64, "bytes": 1024}]}

    return {"schema": "deepresearch-application-packages-v1", "source_sha": "c" * 40,
            "repository": "W205614/DeepResearch", "artifact_release": "artifact-applications-v0.1.1-r1",
            "version": "v0.1.1", "verification": {"licenses": True, "security": True, "runtime": True},
            "images": [{"service": service, "repository": "ghcr.io/w205614/deepresearch-" + service,
                        "image_id": "sha256:" + "d" * 64, "platform": "linux/amd64",
                        "archive_tag": "dr-package-candidate-" + service + ":v0.1.1", **archive(service)}
                       for service in ["agent", "backend", "web"]], "sources": archive("sources")}


def test_accepts_exact_three_image_release_manifest():
    validate_manifest(manifest(), "c" * 40, "artifact-applications-v0.1.1-r1")


def test_new_package_selectors_are_excluded_from_historical_grafana_replay(historical_grafana_maintenance_root):
    from scripts.check_grafana_active_source_retirement import ROOT, selector_files

    current = selector_files(ROOT)
    historical = selector_files(historical_grafana_maintenance_root)
    for path in ['.github/workflows/publish-application-packages.yml', 'deployment/compose/compose.packages.yaml']:
        assert path in current
        assert path not in historical


@pytest.mark.parametrize("change", ["source", "namespace", "duplicate-image", "incomplete-evidence", "path", "duplicate-part", "oversize", "mutable-version"])
def test_rejects_unbound_or_unsafe_publication_input(change):
    data = copy.deepcopy(manifest())
    if change == "source":
        data["source_sha"] = "e" * 40
    elif change == "namespace":
        data["images"][0]["repository"] = "ghcr.io/another-owner/deepresearch-agent"
    elif change == "duplicate-image":
        data["images"][1] = copy.deepcopy(data["images"][0])
    elif change == "incomplete-evidence":
        data["verification"]["licenses"] = False
    elif change == "path":
        data["sources"]["parts"][0]["name"] = "../private"
    elif change == "duplicate-part":
        data["sources"]["parts"][0]["name"] = data["images"][0]["parts"][0]["name"]
    elif change == "oversize":
        data["sources"]["parts"][0]["bytes"] = 65 * 1024**2
    else:
        data["version"] = "latest"
    with pytest.raises(ValueError):
        validate_manifest(data, "c" * 40, "artifact-applications-v0.1.1-r1")
