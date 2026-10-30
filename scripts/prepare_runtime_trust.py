"""Replace a staged runtime's CA or timezone closure with genuine Debian data.

Runs only in an image packaging stage. It never installs a package manager or
changes the live host. Package versions/control records come from dpkg, while
the Docker recipes authenticate downloads through APT and pinned SHA256 values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


REMOVED_CA_SHA256 = frozenset({
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4",
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b",
})
CA_VERSION = "20250419+deb13u1"
TZ_VERSION = "2026e-0+deb13u1"
CA_TREES = ("etc/ssl/certs", "usr/share/ca-certificates", "usr/local/share/ca-certificates")


def pem_fingerprints(pem: bytes) -> set[str]:
    import ssl

    blocks = re.findall(b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    if not blocks:
        raise ValueError("CA payload contains no certificates")
    return {hashlib.sha256(ssl.PEM_cert_to_DER_cert(block.decode("ascii"))).hexdigest()
            for block in blocks}


def certificate_hashes(pem: bytes) -> set[str]:
    hashes = pem_fingerprints(pem)
    if hashes & REMOVED_CA_SHA256:
        raise ValueError("DSA-6572-1 removed trust anchor is still active")
    return hashes


def staged_path(root: Path, relative: str, *, allow_leaf_symlink: bool = False) -> Path:
    if root.is_symlink() or root.absolute() != root.resolve():
        raise ValueError("Runtime staging root must not have symlink aliases")
    name = PurePosixPath(relative)
    if name.is_absolute() or ".." in name.parts:
        raise ValueError("Only runtime-relative packaging paths are permitted")
    target = root.joinpath(*name.parts)
    for parent in target.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Runtime packaging path traverses a symlink")
    if target.is_symlink() and not allow_leaf_symlink:
        raise ValueError("Runtime packaging target must not be a symlink")
    return target


def replace_path(root: Path, relative: str) -> Path:
    # Unlinking a leaf link is safe; following it for reads/writes is not.
    target = staged_path(root, relative, allow_leaf_symlink=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def copy_tree(root: Path, source: Path, relative: str) -> None:
    destination = replace_path(root, relative)
    original = source / relative
    if original.is_dir():
        shutil.copytree(original, destination, symlinks=True)
    else:
        destination.mkdir()


def package_record(package: str, source: Path, control: Path | None = None,
                   expected_version: str | None = None) -> tuple[str, str]:
    if control is None:
        record = subprocess.check_output(["dpkg-query", "-s", package], text=True)
    else:
        record = control.read_text(encoding="utf-8").strip() + "\n"
        # A real data-only installation into a distroless root, retaining the
        # package's original control fields, not a fabricated version override.
        record += "Status: install ok installed\n"
    fields = dict(line.split(": ", 1) for line in record.splitlines() if ": " in line and not line[0].isspace())
    expected = (CA_VERSION if package == "ca-certificates" else TZ_VERSION) if expected_version is None else expected_version
    if fields.get("Package") != package or fields.get("Version") != expected:
        raise ValueError("Installed Debian package identity differs from the reviewed closure")
    return fields["Version"], record


def write_package_record(root: Path, package: str, record: str, source: Path) -> None:
    records = staged_path(root, "var/lib/dpkg/status.d")
    records.mkdir(parents=True, exist_ok=True)
    for path in records.iterdir():
        if (path.is_file() or path.is_symlink()) and (path.name == package or path.name.startswith(package + ".")
                               or path.name.startswith(package + ":")):
            path.unlink()
    (records / package).write_text(record, encoding="utf-8")
    status = staged_path(root, "var/lib/dpkg/status")
    if status.is_file():
        paragraphs = status.read_text(encoding="utf-8").split("\n\n")
        paragraphs = [p for p in paragraphs if not re.search(r"^Package: " + re.escape(package) + r"$", p, re.M)]
        status.write_text("\n\n".join(p.strip() for p in paragraphs if p.strip()) + "\n\n" + record,
                          encoding="utf-8")
    # Preserve real checksums/conffile metadata when dpkg installed the package.
    for suffix in ("md5sums", "conffiles"):
        original = source / ("var/lib/dpkg/info/" + package + "." + suffix)
        if original.is_file():
            shutil.copy2(original, records / (package + "." + suffix))


def tree_manifest(root: Path, relatives: tuple[str, ...]) -> dict:
    result = {}
    for relative in relatives:
        base = staged_path(root, relative, allow_leaf_symlink=True)
        for path in [base, *sorted(base.rglob("*"))] if base.is_dir() and not base.is_symlink() else [base]:
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[name] = {"symlink": os.readlink(path)}
            elif path.is_file():
                result[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def check_active_ca(root: Path) -> dict:
    bundle = staged_path(root, "etc/ssl/certs/ca-certificates.crt")
    roots = certificate_hashes(bundle.read_bytes())
    directory_roots = set()
    for path in bundle.parent.iterdir():
        if path == bundle:
            continue
        # Resolve absolute image symlinks within the staged image, not the host.
        seen = set()
        current = path
        while current.is_symlink():
            if current in seen:
                raise ValueError("CA symlink cycle")
            seen.add(current)
            link = Path(os.readlink(current))
            current = root / str(link).lstrip("/") if link.is_absolute() else current.parent / link
            current = Path(os.path.normpath(current))
            if not current.is_relative_to(root):
                raise ValueError("CA symlink escapes staged runtime")
        if current.is_dir():
            continue
        payload = current.read_bytes()
        # Go's root_unix scans every readable CAPATH file, regardless of name.
        # Ignore non-certificate files explicitly, never by filename suffix.
        if b"-----BEGIN CERTIFICATE-----" not in payload:
            continue
        directory_roots.update(certificate_hashes(payload))
    if not directory_roots or directory_roots != roots:
        raise ValueError("CA bundle and hashed/named certificate directory disagree")
    inactive_payloads = {}
    configuration = (root / "etc/ca-certificates.conf").read_text(encoding="utf-8").splitlines()
    selected = {line.strip() for line in configuration if line.strip() and not line.startswith(("#", "!"))}
    for path in (root / "usr/share/ca-certificates").rglob("*.crt"):
        removed = pem_fingerprints(path.read_bytes()) & REMOVED_CA_SHA256
        if removed:
            entry = path.relative_to(root / "usr/share/ca-certificates").as_posix()
            if entry in selected:
                raise ValueError("Removed packaged CA is still selected by configuration")
            inactive_payloads[path.relative_to(root).as_posix()] = {
                "fingerprints": sorted(removed),
                "config_state": "explicit_disabled" if "!" + entry in configuration else "unselected",
            }
    return {"bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "bundle_root_count": len(roots), "directory_root_count": len(directory_roots),
            "active_removed_anchor_fingerprints_absent": sorted(REMOVED_CA_SHA256),
            "inactive_packaged_removed_anchors": inactive_payloads}


def inspect_python_trust(interpreter: Path) -> dict:
    code = """import certifi,hashlib,httpx,importlib.metadata,json,os,ssl
from pathlib import Path
p=Path(certifi.where());ctx=httpx.create_ssl_context()
print(json.dumps({'certifi_version':importlib.metadata.version('certifi'),
'certifi_bundle':str(p),'certifi_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
'certifi_pem':p.read_text(),'httpx_version':httpx.__version__,
'default_verify_paths':ssl.get_default_verify_paths()._asdict(),
'SSL_CERT_FILE':os.environ.get('SSL_CERT_FILE'),
'httpx_effective_root_sha256':[hashlib.sha256(c).hexdigest() for c in ctx.get_ca_certs(binary_form=True)]}))"""
    value = json.loads(subprocess.check_output([str(interpreter), "-c", code], text=True))
    fingerprints = certificate_hashes(value.pop("certifi_pem").encode("ascii"))
    if REMOVED_CA_SHA256 & set(value["httpx_effective_root_sha256"]):
        raise ValueError("HTTPX default trust still contains a removed CA")
    value["certifi_root_count"] = len(fingerprints)
    value["removed_anchor_fingerprints_absent"] = sorted(REMOVED_CA_SHA256)
    return value


def prepare(root: Path, source: Path, kind: str, control: Path | None = None,
            expected_version: str | None = None) -> dict:
    root, source = root.absolute(), source.absolute()
    if (root == source or root == Path(root.anchor) or root.is_symlink() or root != root.resolve()
            or root.name != "runtime-root"):
        raise ValueError("An explicit isolated runtime-root staging directory is required")
    if not (root / "etc/os-release").exists():
        raise ValueError("The original runtime OS identity must be present")
    package = "ca-certificates" if kind == "ca" else "tzdata"
    version, record = package_record(package, source, control, expected_version)
    if kind == "ca":
        for relative in CA_TREES:
            copy_tree(root, source, relative)
        config = replace_path(root, "etc/ca-certificates.conf")
        shutil.copy2(source / "etc/ca-certificates.conf", config)
        # Replace alternate OpenSSL entry points as well as its bundle/CAPATH.
        for relative, link in (("usr/lib/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"),
                               ("usr/lib/ssl/certs", "/etc/ssl/certs"),
                               ("etc/ssl/cert.pem", "certs/ca-certificates.crt")):
            replace_path(root, relative).symlink_to(link)
        verification = check_active_ca(root)
        paths = (*CA_TREES, "etc/ca-certificates.conf", "usr/lib/ssl/cert.pem", "usr/lib/ssl/certs",
                 "etc/ssl/cert.pem")
    else:
        copy_tree(root, source, "usr/share/zoneinfo")
        utc = root / "usr/share/zoneinfo/Etc/UTC"
        if not utc.is_file() or utc.read_bytes()[:4] != b"TZif":
            raise ValueError("Genuine tzdata UTC payload is missing")
        replace_path(root, "etc/localtime").symlink_to("/usr/share/zoneinfo/Etc/UTC")
        replace_path(root, "etc/timezone").write_text("Etc/UTC\n", encoding="utf-8")
        paths = ("usr/share/zoneinfo", "etc/timezone", "etc/localtime")
        verification = {"UTC_TZif_present": True,
                        "timezone_payload_only_OS_support_not_assessed_here": True}
    write_package_record(root, package, record, source)
    report = {"package": package, "version": version, "verification": verification,
              "payload": tree_manifest(root, paths), "metadata_only_fix": False}
    if kind == "tz-with-legacy":
        legacy_version, legacy_record = package_record("tzdata-legacy", source, expected_version=expected_version)
        write_package_record(root, "tzdata-legacy", legacy_record, source)
        report["additional_packages"] = {"tzdata-legacy": legacy_version}
    output = staged_path(root, "usr/share/deepresearch/" + package + "-closure.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--source-root", default="/", type=Path)
    parser.add_argument("--kind", required=True, choices=("ca", "tz", "tz-with-legacy"))
    parser.add_argument("--control", type=Path)
    parser.add_argument("--expected-version", help="Pinned Debian package version for this runtime release")
    args = parser.parse_args()
    print(json.dumps(prepare(args.runtime_root, args.source_root, args.kind, args.control,
                             args.expected_version)["verification"]))
