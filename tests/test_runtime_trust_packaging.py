"""Direct staged data/metadata tests; no Docker, APT, HTTP or live runtime."""
import hashlib
from pathlib import Path
import re
import sys

import certifi
import pytest

from scripts import prepare_runtime_trust as trust


FIXTURES = Path(__file__).parent / "fixtures/runtime_trust"


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root, source = tmp_path / "runtime-root", tmp_path / "package-source"
    (root / "etc").mkdir(parents=True)
    (root / "etc/os-release").write_text("ID=debian\nVERSION_ID=13\n")
    for tree in trust.CA_TREES:
        (source / tree).mkdir(parents=True, exist_ok=True)
    pem = re.search(b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                    Path(certifi.where()).read_bytes(), re.S).group() + b"\n"
    (source / "etc/ssl/certs/ca-certificates.crt").write_bytes(pem)
    (source / "etc/ssl/certs/current.pem").write_bytes(pem)
    (source / "etc/ssl/certs/12345678.0").write_bytes(pem)
    (source / "usr/share/ca-certificates/current.crt").write_bytes(pem)
    (source / "etc/ca-certificates.conf").write_text("current.crt\n")

    def actual_record(package, *_):
        version = trust.CA_VERSION if package == "ca-certificates" else trust.TZ_VERSION
        return version, f"Package: {package}\nVersion: {version}\nStatus: install ok installed\n"

    monkeypatch.setattr(trust, "package_record", actual_record)
    return root, source


def require_symlink(directory):
    probe = directory / "symlink-probe"
    try:
        probe.symlink_to("unused-target")
    except OSError:
        pytest.skip("Host cannot create symlinks; Linux image packaging still requires them")


def test_replaces_stale_bundle_capath_payload_and_old_package_record(layout):
    root, source = layout
    require_symlink(root.parent)
    stale = root / "etc/ssl/certs"
    stale.mkdir(parents=True)
    for fixture in FIXTURES.glob("*.crt"):
        (stale / fixture.name).write_bytes(fixture.read_bytes())
        (root / "usr/share/ca-certificates").mkdir(parents=True, exist_ok=True)
        (root / "usr/share/ca-certificates" / fixture.name).write_bytes(fixture.read_bytes())
    records = root / "var/lib/dpkg/status.d"
    records.mkdir(parents=True)
    (records / "ca-certificates").write_text("Package: ca-certificates\nVersion: 20250419\n")
    report = trust.prepare(root, source, "ca")
    assert report["verification"]["bundle_root_count"] == 1
    assert report["verification"]["directory_root_count"] == 1
    assert not any((stale / fixture.name).exists() for fixture in FIXTURES.glob("*.crt"))
    assert not any((root / "usr/share/ca-certificates" / fixture.name).exists()
                   for fixture in FIXTURES.glob("*.crt"))
    assert "Version: " + trust.CA_VERSION in (records / "ca-certificates").read_text()
    assert (root / "usr/lib/ssl/cert.pem").is_symlink()
    assert (root / "usr/lib/ssl/certs").is_symlink()


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.crt")), ids=lambda p: p.stem)
def test_actual_removed_public_anchors_are_rejected_in_bundle_and_capath(layout, fixture):
    root, source = layout
    require_symlink(root.parent)
    with pytest.raises(ValueError, match="removed trust anchor"):
        trust.certificate_hashes(fixture.read_bytes())
    # A clean replacement bundle cannot mask a stale named/hashed root file.
    (source / "etc/ssl/certs/abcdefab.0").write_bytes(fixture.read_bytes())
    with pytest.raises(ValueError, match="removed trust anchor"):
        trust.prepare(root, source, "ca")


@pytest.mark.parametrize("filename", ["removed.crt", "no-extension"])
def test_capath_checks_pem_certificates_under_every_readable_filename(layout, filename):
    root, source = layout
    require_symlink(root.parent)
    (source / "etc/ssl/certs" / filename).write_bytes(
        (FIXTURES / "Comodo_AAA_Services_root.crt").read_bytes())
    with pytest.raises(ValueError, match="removed trust anchor"):
        trust.prepare(root, source, "ca")


@pytest.mark.parametrize("leaf", ["status.d", "status"])
def test_package_metadata_leaf_symlinks_never_write_outside_stage(layout, tmp_path, leaf):
    root, source = layout
    require_symlink(root.parent)
    outside = tmp_path / ("outside-" + leaf)
    if leaf == "status.d":
        outside.mkdir()
    else:
        outside.write_text("protected original")
    destination = root / "var/lib/dpkg" / leaf
    destination.parent.mkdir(parents=True)
    destination.symlink_to(outside, target_is_directory=leaf == "status.d")
    with pytest.raises(ValueError, match="target must not be a symlink"):
        trust.write_package_record(root, "ca-certificates", "unused", source)
    assert (list(outside.iterdir()) == [] if outside.is_dir() else outside.read_text() == "protected original")


def test_runtime_root_alias_is_rejected(layout, tmp_path):
    root, source = layout
    require_symlink(root.parent)
    alias = tmp_path / "root-alias"
    alias.symlink_to(root.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="isolated runtime-root"):
        trust.prepare(alias / "runtime-root", source, "ca")


def test_metadata_only_or_wrong_suite_package_is_rejected(tmp_path):
    control = tmp_path / "control"
    control.write_text("Package: tzdata\nVersion: 2026e-0+deb12u1\nArchitecture: all\n")
    with pytest.raises(ValueError, match="identity differs"):
        trust.package_record("tzdata", tmp_path, control)


def test_timezone_replaces_data_and_its_real_record_without_changing_os(layout):
    root, source = layout
    require_symlink(root.parent)
    old_os = (root / "etc/os-release").read_bytes()
    (root / "usr/share/zoneinfo").mkdir(parents=True)
    (root / "usr/share/zoneinfo/stale-zone").write_bytes(b"old")
    zone = source / "usr/share/zoneinfo/Etc/UTC"
    zone.parent.mkdir(parents=True)
    zone.write_bytes(b"TZif2\0synthetic-data-only-copy-fixture")
    result = trust.prepare(root, source, "tz")
    assert not (root / "usr/share/zoneinfo/stale-zone").exists()
    assert (root / "usr/share/zoneinfo/Etc/UTC").read_bytes() == zone.read_bytes()
    assert result["payload"]["usr/share/zoneinfo/Etc/UTC"]["sha256"] == hashlib.sha256(zone.read_bytes()).hexdigest()
    assert trust.TZ_VERSION in (root / "var/lib/dpkg/status.d/tzdata").read_text()
    assert (root / "etc/os-release").read_bytes() == old_os
    assert result["verification"]["timezone_payload_only_OS_support_not_assessed_here"] is True


def test_staging_guard_rejects_live_root_and_symlink_escape(layout, tmp_path):
    root, source = layout
    require_symlink(root.parent)
    with pytest.raises(ValueError, match="isolated runtime-root"):
        trust.prepare(source, source, "ca")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="traverses a symlink"):
        trust.staged_path(root, "redirect/certificate")
    assert list(outside.iterdir()) == []


def test_locked_actual_certifi_and_default_httpx_trust_have_no_removed_anchor():
    result = trust.inspect_python_trust(Path(sys.executable))
    assert result["certifi_version"] == "2026.10.23"
    assert result["certifi_sha256"] == "2ef52ad56b4803e264045c915e55c236cbfa045b84b81efefc218a19eb44bd2b"
    assert result["certifi_root_count"] > 0
    assert not set(result["httpx_effective_root_sha256"]) & trust.REMOVED_CA_SHA256
