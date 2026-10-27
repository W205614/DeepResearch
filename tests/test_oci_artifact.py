import gzip
import hashlib
import io
import json
import tarfile

import pytest

from scripts.oci_artifact import HARD_ARCHIVE_LIMIT, OCIArtifactError, verify_oci_archive
from scripts.image_disposition import MAX_ARCHIVE_BYTES


def fixture(
    tmp_path, specs, *, compression="raw", declared_bad=False, legacy_bad=False, conflicting_reference=False
):
    blobs = {}

    def digest(data):
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def add(data, media):
        key = digest(data)
        blobs[key] = data
        return {"digest": key, "size": len(data), "mediaType": media}

    layers = []
    diffs = []
    for entries in specs:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for name, kind, data in entries:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                elif kind == "directory":
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
                else:
                    info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                    info.linkname = data
                    tar.addfile(info)
        raw = stream.getvalue()
        diffs.append("sha256:" + "f" * 64 if declared_bad else digest(raw))
        encoded = gzip.compress(raw, mtime=0) if compression == "gzip" else raw
        media = (
            "application/vnd.oci.image.layer.v1.tar"
            + {"gzip": "+gzip", "raw": "", "unknown": "+zstd"}[compression]
        )
        layers.append(add(encoded, media))
    cfg = add(
        json.dumps(
            {"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": diffs}}
        ).encode(),
        "application/vnd.oci.image.config.v1+json",
    )
    mf = add(
        json.dumps({"config": cfg, "layers": layers}).encode(), "application/vnd.oci.image.manifest.v1+json"
    )
    manifests = [dict(mf, platform={"architecture": "amd64", "os": "linux"})]
    if conflicting_reference:
        attestation = add(
            json.dumps({"config": dict(cfg, size=cfg["size"] + 1), "layers": []}).encode(),
            "application/vnd.oci.image.manifest.v1+json",
        )
        manifests.append(dict(attestation, platform={"architecture": "unknown", "os": "unknown"}))
    index = add(json.dumps({"manifests": manifests}).encode(), "application/vnd.oci.image.index.v1+json")
    outer = {
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        "index.json": json.dumps({"manifests": [index]}).encode(),
    }
    outer.update({"blobs/sha256/" + key[7:]: data for key, data in blobs.items()})
    if legacy_bad:
        outer["manifest.json"] = json.dumps([{"Config": "wrong-config", "Layers": []}]).encode()
    path = tmp_path / "synthetic-not-approved.oci.tar"
    with tarfile.open(path, "w") as tar:
        for name, data in outer.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    expected = {
        "archive_bytes": path.stat().st_size,
        "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "index_digest": index["digest"],
        "platform_manifest_digest": mf["digest"],
        "config_digest": cfg["digest"],
        "diff_ids": diffs,
        "binary_sha256": {"usr/bin/app": hashlib.sha256(b"expected ELF").hexdigest()},
    }
    return path, expected


BASE = [("usr/bin/app", "file", b"expected ELF")]


@pytest.mark.parametrize("compression", ["raw", "gzip"])
def test_actual_uncompressed_diffids_are_recomputed_and_no_authorization_is_granted(tmp_path, compression):
    path, expected = fixture(tmp_path, [BASE], compression=compression)
    facts = verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)
    assert facts["recomputed_diff_ids"] == expected["diff_ids"]
    assert facts["uncompressed_layer_bytes"] == 10240
    assert facts["authorization_evaluated"] is False


@pytest.mark.parametrize("compression", ["raw", "gzip"])
def test_self_consistent_but_false_config_diffid_is_rejected(tmp_path, compression):
    path, expected = fixture(tmp_path, [BASE], compression=compression, declared_bad=True)
    with pytest.raises(OCIArtifactError, match="DiffID"):
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)


@pytest.mark.parametrize(
    "upper",
    [
        [("usr/bin/.wh.app", "file", b"")],
        [("usr/bin/.wh..wh..opq", "file", b"")],
        [("usr/bin", "symlink", "/other")],
        [("usr/bin/app", "symlink", "/other")],
        [("usr/bin/app", "hardlink", "other")],
    ],
)
def test_deleted_or_link_replaced_target_cannot_inherit_lower_elf_hash(tmp_path, upper):
    path, expected = fixture(tmp_path, [BASE, upper])
    with pytest.raises(OCIArtifactError):
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)


@pytest.mark.parametrize("whiteout", [".wh.app", ".wh..wh..opq"])
def test_whiteouts_do_not_remove_new_same_layer_files_regardless_tar_order(tmp_path, whiteout):
    path, expected = fixture(tmp_path, [BASE, [BASE[0], ("usr/bin/" + whiteout, "file", b"")]])
    assert (
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)["binary_sha256"]
        == expected["binary_sha256"]
    )


def test_non_directory_ancestor_blocks_even_when_child_bytes_match(tmp_path):
    path, expected = fixture(tmp_path, [[("usr/bin", "symlink", "/other"), BASE[0]]])
    with pytest.raises(OCIArtifactError, match="ancestor"):
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)


@pytest.mark.parametrize(
    "case",
    [
        "unknown_compression",
        "unsafe_path",
        "different_legacy_manifest",
        "uncompressed_bound",
        "archive_bound",
        "invalid_policy_cap",
    ],
)
def test_unknown_or_unsafe_archive_semantics_fail_closed(tmp_path, case):
    specs = [BASE + ([("../escape", "file", b"x")] if case == "unsafe_path" else [])]
    path, expected = fixture(
        tmp_path,
        specs,
        compression="unknown" if case == "unknown_compression" else "raw",
        legacy_bad=case == "different_legacy_manifest",
    )
    kwargs = {"max_archive_bytes": 512 * 1024**2}
    if case == "uncompressed_bound":
        kwargs["max_uncompressed_bytes"] = 1
    if case == "archive_bound":
        kwargs["max_archive_bytes"] = 1
    if case == "invalid_policy_cap":
        kwargs["max_archive_bytes"] = HARD_ARCHIVE_LIMIT + 1
    with pytest.raises(OCIArtifactError):
        verify_oci_archive(path, expected, **kwargs)


def test_future_two_gib_cap_does_not_change_minio_default_policy():
    assert HARD_ARCHIVE_LIMIT == 2 * 1024**3
    assert MAX_ARCHIVE_BYTES == 512 * 1024**2


def test_repeated_blob_reference_cannot_change_descriptor_size(tmp_path):
    path, expected = fixture(tmp_path, [BASE], conflicting_reference=True)
    with pytest.raises(OCIArtifactError, match="size"):
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)


def test_explicit_directory_restoration_removes_old_ancestor_link(tmp_path):
    path, expected = fixture(
        tmp_path, [BASE, [("usr/bin", "symlink", "/other")], [("usr/bin", "directory", b""), BASE[0]]]
    )
    assert (
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)["binary_sha256"]
        == expected["binary_sha256"]
    )


@pytest.mark.parametrize("leaf", [".wh.", ".wh..", ".wh..."])
def test_whiteout_must_name_a_real_child_not_empty_dot_or_parent(tmp_path, leaf):
    path, expected = fixture(tmp_path, [BASE, [("usr/bin/" + leaf, "file", b"")]])
    with pytest.raises(OCIArtifactError, match="whiteout child"):
        verify_oci_archive(path, expected, max_archive_bytes=512 * 1024**2)
