"""Build an isolated candidate after checking its exact retained asset source."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parent


def inspect(image):
    result = subprocess.run(["docker", "image", "inspect", image], check=True, capture_output=True)
    rows = json.loads(result.stdout)
    if len(rows) != 1:
        raise ValueError("Expected exactly one local image")
    return rows[0]


def source_state():
    scopes = [".dockerignore", ".gitattributes", "scripts/oci_artifact.py", "deployment/vendors/grafana-20261107", "deployment/docker/Dockerfile.grafana-20261107",
              "deployment/vendors/grafana/main", "deployment/vendors/grafana/reviewed_backend_verifier.go",
              "deployment/vendors/grafana/reviewed_backend_verifier_test.go"]
    files = sorted({file for scope in scopes for file in ((ROOT / scope).rglob("*") if (ROOT / scope).is_dir() else [ROOT / scope])
                    if file.is_file() and "__pycache__" not in file.parts})
    hashes = {file.relative_to(ROOT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *scopes], cwd=ROOT, text=True)
    workspace_dirty = bool(subprocess.check_output(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=ROOT, text=True))
    aggregate = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"project_source_commit": head, "input_status": dirty, "inputs_committed": not dirty,
            "workspace_dirty": workspace_dirty, "input_files_sha256": hashes, "aggregate_inputs_sha256": aggregate,
            "upstream_grafana_commit": "ce471d869a4810cd83c987dd3521c4b67689dbb6"}


def validate_committed_input_bytes(provenance):
    normalizations = {}
    for name, expected in provenance["input_files_sha256"].items():
        result = subprocess.run(["git", "cat-file", "blob", provenance["project_source_commit"] + ":" + name],
                                cwd=ROOT, capture_output=True)
        if result.returncode:
            raise ValueError("Candidate input is absent from the captured project commit: " + name)
        committed_sha = hashlib.sha256(result.stdout).hexdigest()
        if committed_sha == expected:
            continue
        actual = (ROOT / name).read_bytes()
        if hashlib.sha256(actual).hexdigest() != expected or actual.replace(b"\r\n", b"\n") != result.stdout:
            raise ValueError("Working input bytes differ from the exact committed Git blob: " + name)
        attribute_raw = subprocess.check_output(["git", "check-attr", "-z", "text", "eol", "filter", "working-tree-encoding", "--", name], cwd=ROOT).decode()
        fields = attribute_raw.rstrip("\0").split("\0")
        attributes = {fields[index + 1]: fields[index + 2] for index in range(0, len(fields), 3)}
        eol = subprocess.check_output(["git", "ls-files", "--eol", "--", name], cwd=ROOT, text=True).strip()
        classification = eol.split("\t", 1)[0].split()
        if (attributes["text"] == "unset" or attributes["filter"] not in ("unspecified", "unset")
                or attributes["working-tree-encoding"] not in ("unspecified", "unset")
                or len(classification) < 2 or classification[0] != "i/lf"
                or classification[1] not in ("w/crlf", "w/mixed") or b"\0" in actual):
            raise ValueError("Only Git-classified text permits CRLF-to-LF normalization: " + name)
        cleaned = subprocess.run(["git", "hash-object", "--stdin", "--path=" + name], input=actual,
                                 cwd=ROOT, capture_output=True, check=True).stdout.decode().strip()
        object_id = subprocess.check_output(["git", "rev-parse", provenance["project_source_commit"] + ":" + name],
                                            cwd=ROOT, text=True).strip()
        if cleaned != object_id:
            raise ValueError("Git does not normalize these input bytes to the captured committed blob: " + name)
        normalizations[name] = {"working_sha256": expected, "committed_sha256": committed_sha,
                                "git_blob_object_id": object_id, "git_attributes": attributes, "git_eol_classification": eol,
                                "normalization": "Git-classified text CRLF-to-LF only"}
    return normalizations


def validate_image_binding(image, metadata, provenance):
    expected_revision = provenance["project_source_commit"]
    if not provenance["inputs_committed"]:
        expected_revision += "+uncommitted-inputs"
    labels = image["Config"].get("Labels", {})
    if (labels.get("org.opencontainers.image.revision") != expected_revision
            or labels.get("org.deepresearch.project.source_revision") != expected_revision
            or labels.get("org.deepresearch.project.inputs_sha256") != provenance["aggregate_inputs_sha256"]
            or labels.get("org.deepresearch.upstream.grafana_revision") != provenance["upstream_grafana_commit"]):
        raise ValueError("Actual candidate image labels differ from its captured project source inputs")
    if image["Id"] != metadata["containerimage.digest"]:
        raise ValueError("Candidate tag no longer identifies this invocation's immutable BuildKit image")


def verify_exported_archive(archive, image, metadata):
    sys.path.insert(0, str(ROOT))
    from scripts.oci_artifact import verify_oci_archive

    if image["Os"] != "linux" or image["Architecture"] != "amd64":
        raise ValueError("Captured candidate must be the exact Linux amd64 runtime platform")
    if (metadata["containerimage.digest"] != image["Id"]
            or metadata["containerimage.descriptor"] != image["Descriptor"]
            or image["Descriptor"]["digest"] != image["Id"]):
        raise ValueError("Captured BuildKit index/descriptor and immutable image identity differ")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with tarfile.open(archive, "r:") as tar:
        index = json.load(tar.extractfile("index.json"))
        roots = index["manifests"]
        if len(roots) != 1 or roots[0]["digest"] != image["Id"]:
            raise ValueError("Exported OCI root differs from the captured immutable candidate image")

        def document(descriptor):
            return json.load(tar.extractfile("blobs/sha256/" + descriptor["digest"].removeprefix("sha256:")))

        nested = document(roots[0])
        platforms = [row for row in nested["manifests"] if row.get("platform") == {"os": "linux", "architecture": "amd64"}]
        if len(platforms) != 1:
            raise ValueError("Candidate archive must contain one exact Linux amd64 runtime manifest")
        manifest = document(platforms[0])
        if ("containerimage.config.digest" in metadata
                and manifest["config"]["digest"] != metadata["containerimage.config.digest"]):
            raise ValueError("Exported OCI config differs from the actual BuildKit result")
    subject = {"index_digest": image["Id"], "platform_manifest_digest": platforms[0]["digest"],
               "config_digest": manifest["config"]["digest"], "diff_ids": image["RootFS"]["Layers"],
               "archive_sha256": digest, "archive_bytes": archive.stat().st_size, "binary_sha256": {}}
    facts = verify_oci_archive(archive, subject, max_archive_bytes=2 * 1024**3)
    return subject, facts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tag", default="deepresearch-grafana-candidate:20261107")
    parser.add_argument("--allow-uncommitted-inputs", action="store_true", help="Validation build only; cannot serve as a final exact-source candidate")
    args = parser.parse_args()
    if not args.tag.startswith("deepresearch-grafana-candidate:"):
        raise ValueError("Use the isolated deepresearch-grafana-candidate tag namespace")
    output = args.output_dir.resolve()
    if not output.is_relative_to((ROOT / ".cache").resolve()) or output.exists():
        raise ValueError("Use a new output directory inside the repository .cache")
    provenance = source_state()
    if not provenance["inputs_committed"] and not args.allow_uncommitted_inputs:
        raise ValueError("Commit the candidate inputs before final packaging; validation builds require --allow-uncommitted-inputs")
    normalizations = validate_committed_input_bytes(provenance) if provenance["inputs_committed"] else {}
    provenance["git_text_normalizations"] = normalizations
    provenance["committed_source_identity_verified"] = provenance["inputs_committed"]
    provenance["git_normalized_text_inputs_equal_committed_blobs"] = provenance["inputs_committed"]
    provenance["input_worktree_bytes_equal_committed_blobs"] = provenance["inputs_committed"] and not normalizations
    source = json.loads((SOURCE / "source.lock.json").read_bytes())
    expected = source["complete_plugin_assets_source"].split("@", 1)[1]
    retained = "deepresearch-approved-grafana:20261105"
    before = inspect(retained)
    if before["Id"] != expected or before["Os"] != "linux" or before["Architecture"] != "amd64":
        raise ValueError("Retained local asset image differs from the exact pinned source")
    output.mkdir(parents=True)
    (output / "project-source-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (output / "retained-source-image-inspect.json").write_text(json.dumps(before, indent=2) + "\n")
    transport = ROOT / ".cache/grafana-20261107/module-proxy"
    subprocess.run([sys.executable, str(SOURCE / "prefetch_modules.py"), "--output", str(transport)], check=True)
    transport_receipt = output / "official-module-transport.json"
    shutil.copyfile(transport.parent / "official-module-transport.json", transport_receipt)
    command = ["docker", "buildx", "build", "--progress", "plain", "--load", "--metadata-file", str(output / "build-metadata.json"),
               "--build-context", "moduletransport=" + str(transport),
               "--build-arg", "RETAINED_ASSET_IMAGE=" + retained,
               "--build-arg", "PROJECT_SOURCE_REVISION=" + provenance["project_source_commit"] + ("" if provenance["inputs_committed"] else "+uncommitted-inputs"),
               "--build-arg", "PROJECT_INPUTS_SHA256=" + provenance["aggregate_inputs_sha256"], "-t", args.tag,
               "-f", "deployment/docker/Dockerfile.grafana-20261107", "."]
    with (output / "build.log").open("wb") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
    after = source_state()
    if after["project_source_commit"] != provenance["project_source_commit"] or after["input_files_sha256"] != provenance["input_files_sha256"] or after["inputs_committed"] != provenance["inputs_committed"]:
        raise ValueError("Project source inputs changed during candidate build")
    if inspect(retained)["Id"] != expected:
        raise ValueError("Retained asset source changed while building")
    image = inspect(args.tag)
    metadata = json.loads((output / "build-metadata.json").read_bytes())
    validate_image_binding(image, metadata, provenance)
    (output / "image-inspect.json").write_text(json.dumps(image, indent=2) + "\n")
    archive = output / "candidate.oci.tar"
    subprocess.run(["docker", "image", "save", "--output", str(archive), image["Id"]], check=True)
    subject, archive_facts = verify_exported_archive(archive, image, metadata)
    (output / "oci-integrity.json").write_text(json.dumps(archive_facts, indent=2) + "\n")
    receipt = {"project_source_commit": provenance["project_source_commit"], "inputs_committed": provenance["inputs_committed"],
               "aggregate_inputs_sha256": provenance["aggregate_inputs_sha256"],
               "input_worktree_bytes_equal_committed_blobs": provenance["input_worktree_bytes_equal_committed_blobs"],
               "committed_source_identity_verified": provenance["committed_source_identity_verified"],
               "git_normalized_text_inputs_equal_committed_blobs": provenance["git_normalized_text_inputs_equal_committed_blobs"],
               "git_text_normalizations": provenance["git_text_normalizations"],
               "public_transport_receipt_sha256": hashlib.sha256(transport_receipt.read_bytes()).hexdigest(), "actual_image_id": image["Id"], "retained_source_image_id": expected,
               "subject": subject, "archive_sha256": subject["archive_sha256"], "archive_bytes": subject["archive_bytes"],
               "artifact_approved": False, "authorization_inherited": False,
               "published": False, "daily_deployment_performed": False,
               "next_step": "Full fresh Trivy and pinned Syft/Grype collection, exact technical review and renewed subject authorization."}
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
