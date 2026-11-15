"""Prepare a local SoupSieve4.1 candidate on the byte-verified v0.1.4 native base.

This layered snapshot is not a redistribution artifact. Final packaging must flatten
its merged filesystem and supply the new wheel's license/source material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_agent_full_scope as gate  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402

BASE = "sha256:fb5ee66cec06d55ce01e0f43243048f2104423fe9a4ed5cef9b1c2312f75fcd1"
BASE_REFERENCE = "dr-package-candidate-agent:v0.1.4-0b613f1386bc"
BASE_CONFIG = "sha256:c22727159cfa4ebeda1a81c24f25caf1accfa426e7367bb6fe0a09d101ae8a9b"
UV = "ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0"
OLD_PROFILE = "deployment/security/agent-record-metadata-profile-20261113.json"
OLD_PROFILE_SHA = "737170a810dabc61edb9bcedfd02b949f9f447b746051cb828f4ab439170dd6b"
PREFIX = "opt/venv/lib/python3.11/site-packages/"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def inventory(image, output):
    script = (
        ROOT / "deployment/security/agent-record-metadata-20261113-evidence/runtime_inventory_only.py.txt"
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--entrypoint",
            "python",
            image,
            "-c",
            script.read_text(encoding="utf-8"),
        ],
        capture_output=True,
        check=True,
        timeout=240,
    )
    output.write_bytes(result.stdout)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", default="dr-v015-agent-soup41:20261115")
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / ".cache").resolve()) or out.exists():
        raise ValueError("Use a new evidence directory under this checkout .cache")
    out.mkdir(parents=True)
    assert sha(ROOT / OLD_PROFILE) == OLD_PROFILE_SHA
    old = json.loads((ROOT / OLD_PROFILE).read_bytes())
    (base,) = json.loads(subprocess.check_output(["docker", "image", "inspect", BASE]))
    assert base["Id"] == BASE and base["Os"] == "linux" and base["Architecture"] == "amd64"
    (reference,) = json.loads(subprocess.check_output(["docker", "image", "inspect", BASE_REFERENCE]))
    assert reference["Id"] == BASE
    base_raw = gate.capture_actual_config(base)
    (out / "base-image-config.raw.json").write_bytes(base_raw)
    assert "sha256:" + hashlib.sha256(base_raw).hexdigest() == BASE_CONFIG
    write(out / "base-inspection.json", base)
    before_probe = gate.probe_image(BASE, old)
    write(out / "base-runtime-probe.json", before_probe)
    assert before_probe["native_elf_closure"] == old["native_elf_closure"]
    assert before_probe["python_source_closure"] == old["python_source_closure"]
    before = inventory(BASE, out / "base-runtime-inventory.json")
    before_facts = capture(BASE, out / "base-filesystem")
    context = out / "context"
    context.mkdir()
    locks = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(p for p in locks["package"] if p["name"] == "soupsieve")
    assert package["version"] == "4.1"
    (wheel,) = package["wheels"]
    archive = context / wheel["url"].rsplit("/", 1)[-1]
    with urlopen(wheel["url"], timeout=60) as response:
        assert response.status == 200
        archive.write_bytes(response.read())
    assert sha(archive) == wheel["hash"].split(":")[1] and archive.stat().st_size == wheel["size"]
    write(out / "wheel-source.json", package)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, context / name)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    fingerprint = (
        "import pathlib,hashlib,json; r=pathlib.Path('/app'); m={str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p!=r/'source-fingerprint.json'}; (r/'source-fingerprint.json').write_text(json.dumps(m,sort_keys=True),encoding='utf-8'); pathlib.Path('/tmp/"
        + archive.name
        + "').unlink()"
    )
    lines = [
        "FROM " + UV + " AS installer",
        "FROM " + BASE_REFERENCE,
        "USER 0:0",
        "COPY " + archive.name + " /tmp/" + archive.name,
        "RUN --mount=type=bind,from=installer,source=/uv,target=/tmp/uv,ro "
        + json.dumps(
            [
                "/tmp/uv",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-cache",
                "--python",
                "/opt/venv/bin/python",
                "/tmp/" + archive.name,
            ]
        ),
        "COPY pyproject.toml uv.lock /app/",
        "RUN " + json.dumps(["python", "-c", fingerprint]),
        "LABEL org.deepresearch.project.source_revision=" + revision,
        'LABEL org.deepresearch.runtime.snapshot-kind="local-soupsieve41-upgrade-not-publication"',
        "USER 10001:10001",
        "",
    ]
    (context / "Dockerfile").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    command = [
        "docker",
        "buildx",
        "build",
        "--load",
        "--network=none",
        "--pull=false",
        "--progress=plain",
        "--tag",
        args.tag,
        str(context),
    ]
    write(out / "build-command.json", command)
    with (out / "build.log").open("wb") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
    (actual,) = json.loads(subprocess.check_output(["docker", "image", "inspect", args.tag]))
    (reference_after,) = json.loads(subprocess.check_output(["docker", "image", "inspect", BASE_REFERENCE]))
    assert reference_after["Id"] == BASE
    assert {k: v for k, v in actual["Config"].items() if k != "Labels"} == {
        k: v for k, v in base["Config"].items() if k != "Labels"
    }
    after_probe = gate.probe_image(actual["Id"], old)
    write(out / "runtime-probe.json", after_probe)
    after = inventory(actual["Id"], out / "runtime-inventory.json")
    assert (
        before["native_files"] == after["native_files"]
        and after["native_elf_closure"] == old["native_elf_closure"]
    )
    python_before = {r["path"]: r["sha256"] for r in before["python_files"]}
    python_after = {r["path"]: r["sha256"] for r in after["python_files"]}
    python_delta = {
        p: {"before": python_before.get(p), "after": python_after.get(p)}
        for p in python_before.keys() | python_after.keys()
        if python_before.get(p) != python_after.get(p)
    }
    assert python_delta and all(p.startswith("/" + PREFIX + "soupsieve/") for p in python_delta)
    after_facts = capture(actual["Id"], out / "candidate-filesystem")
    old_files = {r["path"]: r for r in before_facts["runtime_manifest"]}
    new_files = {r["path"]: r for r in after_facts["runtime_manifest"]}
    delta = {
        p: {"before": old_files.get(p), "after": new_files.get(p)}
        for p in old_files.keys() | new_files.keys()
        if old_files.get(p) != new_files.get(p)
    }

    def allowed(p):
        return (
            p in {"app/pyproject.toml", "app/uv.lock", "app/source-fingerprint.json"}
            or p.startswith(PREFIX + "soupsieve/")
            or p.startswith(PREFIX + "soupsieve-")
        )

    unexpected = [p for p in delta if not allowed(p)]
    write(out / "filesystem-delta.json", {"changes": delta, "unexpected_paths": unexpected})
    assert not unexpected, unexpected
    config_raw = gate.capture_actual_config(actual)
    (out / "image-config.raw.json").write_bytes(config_raw)
    write(out / "image-inspection.json", actual)
    inputs = {
        name: sha(ROOT / name)
        for name in ("pyproject.toml", "uv.lock", "Dockerfile", "scripts/prepare_agent_soupsieve_20261115.py")
    }
    receipt = {
        "prepared_local_candidate": True,
        "published": False,
        "binary_redistribution_approved": False,
        "layered_snapshot_requires_final_flattening": True,
        "source_sha": revision,
        "source_inputs": inputs,
        "image": args.tag,
        "engine_id": actual["Id"],
        "portable_config_digest": "sha256:" + hashlib.sha256(config_raw).hexdigest(),
        "diff_ids": actual["RootFS"]["Layers"],
        "base_engine_id": BASE,
        "base_portable_config_digest": BASE_CONFIG,
        "old_profile": {"path": OLD_PROFILE, "sha256": OLD_PROFILE_SHA},
        "all515_native_files_byte_identical": True,
        "native_elf_closure": after["native_elf_closure"],
        "python_source_closure": after["python_source_closure"],
        "python_file_delta": python_delta,
        "complete_filesystem_expected_delta_only": True,
        "runtime_config_except_labels_identical": True,
        "wheel_sha256": sha(archive),
        "paid_requests": 0,
    }
    write(out / "candidate-receipt.json", receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k != "python_file_delta"}))


if __name__ == "__main__":
    main()
