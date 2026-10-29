"""Pure bounded OCI integrity verification. This module grants no authorization or disposition."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

HARD_ARCHIVE_LIMIT = 2 * 1024**3
MAX_METADATA = 4 * 1024**2
INDEX_TYPES = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}
MANIFEST_TYPES = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}
CONFIG_TYPES = {"application/vnd.oci.image.config.v1+json", "application/vnd.docker.container.image.v1+json"}
RAW_LAYERS = {"application/vnd.oci.image.layer.v1.tar", "application/vnd.oci.image.layer.nondistributable.v1.tar", "application/vnd.docker.image.rootfs.diff.tar"}
GZIP_LAYERS = {"application/vnd.oci.image.layer.v1.tar+gzip", "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip", "application/vnd.docker.image.rootfs.diff.tar.gzip", "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip"}
OPAQUE_TYPES = {"application/vnd.in-toto+json", "application/vnd.oci.empty.v1+json"}


class OCIArtifactError(ValueError):
    pass


def sha256_stream(stream):
    hashed = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        hashed.update(block)
    return hashed.hexdigest()


def canonical_path(name):
    if not isinstance(name, str) or len(name) > 4096 or "\\" in name or "\0" in name:
        raise OCIArtifactError("Unsafe OCI path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise OCIArtifactError("Unsafe OCI path")
    return path.as_posix().removeprefix("./").rstrip("/")


def digest_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise OCIArtifactError("Unsupported OCI digest")
    return value


class BoundedHashReader:
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget
        self.hash, self.bytes = hashlib.sha256(), 0

    def read(self, size=-1):
        if size < 0:
            size = 1024 * 1024
        data = self.stream.read(min(size, 1024 * 1024))
        self.bytes += len(data)
        self.budget[0] += len(data)
        if self.budget[0] > self.budget[1]:
            raise OCIArtifactError("Uncompressed OCI layer inventory exceeds its independent bound")
        self.hash.update(data)
        return data


def remove_tree(state, path, *, children_only=False):
    for existing in list(state):
        if existing.startswith(path + "/") or (existing == path and not children_only):
            del state[existing]


def apply_layer(state, entries, whiteouts, opaque):
    # OCI whiteouts affect lower layers only, independent of tar entry order.
    for path in whiteouts:
        remove_tree(state, path)
    for path in opaque:
        if path == ".":
            state.clear()
        else:
            remove_tree(state, path, children_only=True)
    for path, value in sorted(entries.items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0])):
        parents = list(PurePosixPath(path).parents)[:-1]
        for parent in reversed(parents):
            name = parent.as_posix()
            if name not in state:
                state[name] = {"kind": "directory"}
        previous = state.get(path)
        if value["kind"] != "directory" or (previous and previous["kind"] != "directory"):
            remove_tree(state, path)
        state[path] = value


def verify_oci_archive(path, expected, *, max_archive_bytes, max_uncompressed_bytes=8 * 1024**3):
    """Check facts only. The caller must separately establish human approval and review policy."""
    if type(max_archive_bytes) is not int or not 0 < max_archive_bytes <= HARD_ARCHIVE_LIMIT:
        raise OCIArtifactError("Per-policy archive limit must be explicit and at most2GiB")
    if type(max_uncompressed_bytes) is not int or not 0 < max_uncompressed_bytes <= 16 * 1024**3:
        raise OCIArtifactError("Independent uncompressed layer bound is invalid")
    path = Path(path)
    size = expected.get("archive_bytes")
    if type(size) is not int or size < 0 or size > max_archive_bytes or not path.is_file() or path.is_symlink() or path.stat().st_size != size:
        raise OCIArtifactError("OCI archive is missing or outside its exact byte bound")
    with path.open("rb") as stream:
        if sha256_stream(stream) != expected.get("archive_sha256"):
            raise OCIArtifactError("OCI archive bytes differ from the expected factual subject")
    targets = {canonical_path(name): value for name, value in expected.get("binary_sha256", {}).items()}
    if len(targets) != len(expected.get("binary_sha256", {})):
        raise OCIArtifactError("Tracked binary paths are ambiguous")
    for value in targets.values():
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise OCIArtifactError("Tracked executable digest is invalid")
    relevant = set(targets)
    for name in targets:
        relevant.update(p.as_posix() for p in list(PurePosixPath(name).parents)[:-1])
    verified, documents, state, recomputed = set(), {}, {}, []
    budget = [0, max_uncompressed_bytes]
    with tarfile.open(path, "r:") as archive:
        members = {}
        for member in archive:
            name = canonical_path(member.name)
            if name in members or len(members) >= 1000 or (not member.isfile() and not member.isdir()):
                raise OCIArtifactError("OCI outer member inventory is unsafe or ambiguous")
            members[name] = member

        def json_member(name):
            member = members.get(name)
            if not member or not member.isfile() or member.size > MAX_METADATA:
                raise OCIArtifactError("OCI metadata member is absent or oversized")
            return json.load(archive.extractfile(member))

        if json_member("oci-layout").get("imageLayoutVersion") != "1.0.0":
            raise OCIArtifactError("Unknown OCI layout")

        def blob(descriptor, decode=False):
            key = digest_value(descriptor.get("digest"))
            member = members.get("blobs/sha256/" + key[7:])
            if not member or not member.isfile() or type(descriptor.get("size")) is not int or member.size != descriptor["size"]:
                raise OCIArtifactError("Referenced OCI blob is missing or has different size")
            if key not in verified:
                with archive.extractfile(member) as stream:
                    if "sha256:" + sha256_stream(stream) != key:
                        raise OCIArtifactError("Referenced OCI blob content differs")
                verified.add(key)
            if decode:
                if key not in documents:
                    documents[key] = json_member("blobs/sha256/" + key[7:])
                return documents[key]
            return member

        visited = set()
        def visit(descriptor, depth=0):
            if depth > 32:
                raise OCIArtifactError("OCI descriptor graph exceeds its depth bound")
            key = digest_value(descriptor.get("digest"))
            kind = descriptor.get("mediaType")
            if kind not in INDEX_TYPES | MANIFEST_TYPES | CONFIG_TYPES | RAW_LAYERS | GZIP_LAYERS | OPAQUE_TYPES:
                raise OCIArtifactError("Unknown OCI blob/descriptor media type")
            # Repeated references must still agree with actual member sizes/types.
            blob(descriptor)
            if key in visited:
                return
            visited.add(key)
            if kind in INDEX_TYPES | MANIFEST_TYPES:
                document = blob(descriptor, True)
                for child in document.get("manifests", []):
                    visit(child, depth + 1)
                if "config" in document:
                    visit(document["config"], depth + 1)
                for child in document.get("layers", []):
                    visit(child, depth + 1)
            elif kind in CONFIG_TYPES | RAW_LAYERS | GZIP_LAYERS | OPAQUE_TYPES:
                blob(descriptor)
            else:
                raise OCIArtifactError("Unknown OCI blob/descriptor media type")

        root = json_member("index.json")
        roots = root.get("manifests", [])
        layout = expected.get("root_layout", "nested_index")
        if layout == "direct_manifest":
            # BuildKit's single-platform OCI layout puts the manifest directly in
            # index.json. Bind those exact root bytes instead of inventing a blob.
            with archive.extractfile(members["index.json"]) as stream:
                root_digest = "sha256:" + sha256_stream(stream)
            if (root_digest != expected.get("index_digest") or root.get("schemaVersion") != 2
                    or root.get("mediaType") not in INDEX_TYPES or len(roots) != 1
                    or roots[0].get("mediaType") not in MANIFEST_TYPES):
                raise OCIArtifactError("Direct OCI root index differs from the exact factual subject")
            visit(roots[0])
            index = root
        elif layout == "nested_index":
            if len(roots) != 1 or roots[0].get("digest") != expected.get("index_digest") or roots[0].get("mediaType") not in INDEX_TYPES:
                raise OCIArtifactError("OCI root index differs from the exact factual subject")
            visit(roots[0])
            index = blob(roots[0], True)
        else:
            raise OCIArtifactError("Unknown exact OCI root layout")
        platform = [d for d in index["manifests"] if d.get("digest") == expected.get("platform_manifest_digest")]
        if len(platform) != 1 or platform[0].get("platform") != {"os": expected.get("os", "linux"), "architecture": expected.get("architecture", "amd64")} or platform[0].get("mediaType") not in MANIFEST_TYPES:
            raise OCIArtifactError("Exact expected runtime platform is absent")
        manifest = blob(platform[0], True)
        if manifest["config"].get("digest") != expected.get("config_digest") or manifest["config"].get("mediaType") not in CONFIG_TYPES:
            raise OCIArtifactError("Runtime config differs from the exact factual subject")
        config = blob(manifest["config"], True)
        diff_ids = config.get("rootfs", {}).get("diff_ids")
        layers = manifest.get("layers", [])
        if config.get("rootfs", {}).get("type") != "layers" or diff_ids != expected.get("diff_ids") or len(layers) != len(diff_ids):
            raise OCIArtifactError("Runtime layer/DiffID inventory differs")
        if config.get("architecture") != expected.get("architecture", "amd64") or config.get("os") != expected.get("os", "linux"):
            raise OCIArtifactError("Runtime config platform differs")
        if "manifest.json" in members:
            legacy = json_member("manifest.json")
            expected_config = "blobs/sha256/" + expected["config_digest"][7:]
            expected_layers = ["blobs/sha256/" + layer["digest"][7:] for layer in layers]
            if not isinstance(legacy, list) or len(legacy) != 1 or legacy[0].get("Config") != expected_config or legacy[0].get("Layers") != expected_layers:
                raise OCIArtifactError("Docker compatibility manifest differs from the verified OCI runtime")
        for descriptor, declared in zip(layers, diff_ids, strict=True):
            kind = descriptor.get("mediaType")
            if kind not in RAW_LAYERS | GZIP_LAYERS:
                raise OCIArtifactError("Unsupported runtime layer compression")
            compressed = archive.extractfile(blob(descriptor))
            decompressed = gzip.GzipFile(fileobj=compressed) if kind in GZIP_LAYERS else compressed
            reader = BoundedHashReader(decompressed, budget)
            entries, whiteouts, opaque, seen = {}, [], [], {}
            layer = tarfile.open(fileobj=reader, mode="r|")
            count = 0
            for member in layer:
                count += 1
                if count > 200000:
                    raise OCIArtifactError("OCI layer member inventory exceeds its bound")
                name = canonical_path(member.name)
                if name == "." and member.isdir():
                    continue
                if name in seen and not (member.isdir() and seen[name] == "directory"):
                    raise OCIArtifactError("OCI layer has ambiguous duplicate paths")
                seen[name] = "directory" if member.isdir() else "other"
                parent, leaf = PurePosixPath(name).parent.as_posix(), PurePosixPath(name).name
                if leaf.startswith(".wh."):
                    if not member.isfile() or member.size:
                        raise OCIArtifactError("Unsupported OCI whiteout representation")
                    if leaf == ".wh..wh..opq":
                        opaque.append(parent)
                    else:
                        target_name = leaf[4:]
                        if target_name in {"", ".", ".."}:
                            raise OCIArtifactError("Invalid OCI whiteout child name")
                        whiteouts.append((PurePosixPath(parent) / target_name).as_posix())
                    continue
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise OCIArtifactError("Unsupported OCI layer node semantics")
                if name in relevant:
                    if member.isfile():
                        value = {"kind": "file"}
                        if name in targets:
                            value["sha256"] = sha256_stream(layer.extractfile(member))
                    elif member.isdir():
                        value = {"kind": "directory"}
                    else:
                        value = {"kind": "link", "target": member.linkname}
                    entries[name] = value
            # Include every uncompressed tar byte, including trailing zero padding.
            while reader.read(1024 * 1024):
                pass
            layer.close()
            decompressed.close()
            compressed.close()
            actual = "sha256:" + reader.hash.hexdigest()
            if actual != digest_value(declared):
                raise OCIArtifactError("Actual uncompressed layer DiffID differs from the config declaration")
            recomputed.append(actual)
            apply_layer(state, entries, whiteouts, opaque)
        binaries = {}
        for name, expected_hash in targets.items():
            node = state.get(name)
            if not node or node.get("kind") != "file" or node.get("sha256") != expected_hash:
                raise OCIArtifactError("Tracked executable is absent, replaced, or has different final bytes")
            if any(state.get(p.as_posix(), {}).get("kind", "directory") != "directory" for p in list(PurePosixPath(name).parents)[:-1]):
                raise OCIArtifactError("Tracked executable has a non-directory/symlink ancestor; safe resolution is unsupported")
            binaries[name] = node["sha256"]
    return {"index_digest": expected["index_digest"], "platform_manifest_digest": expected["platform_manifest_digest"], "config_digest": expected["config_digest"], "archive_sha256": expected["archive_sha256"], "binary_sha256": binaries, "verified_blobs": len(verified), "recomputed_diff_ids": recomputed, "uncompressed_layer_bytes": budget[0], "archive_limit_bytes": max_archive_bytes, "uncompressed_limit_bytes": max_uncompressed_bytes, "authorization_evaluated": False}
