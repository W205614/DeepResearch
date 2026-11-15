"""Collect complete current Go source graphs without claiming native runtime acceptance."""

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GO = Path("go")
OUT = ROOT / ".cache/release-v015-20261115/current-contexts"
sys.path.insert(0, str(ROOT))
from scripts.go_stdlib_rebuild_20261112 import decode_graph, require  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf8")


def checked_run(command, cwd, env, stem):
    with (OUT / (stem + ".raw")).open("wb") as out, (OUT / (stem + ".stderr")).open("wb") as err:
        result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, stdout=out, stderr=err)
    if result.returncode:
        raise ValueError(stem + ": exit " + str(result.returncode))
    return (OUT / (stem + ".raw")).read_bytes()


def module_source(stem, module, version, expected_sum, expected_commit, tool):
    env = os.environ.copy()
    env.update(GOTOOLCHAIN=tool, GOPROXY="https://proxy.golang.org", GOSUMDB="sum.golang.org")
    raw = checked_run([GO, "mod", "download", "-json", module + "@" + version], ROOT, env, stem + "-download")
    receipt = json.loads(raw)
    require(not receipt.get("Error"), receipt)
    if expected_sum:
        require(receipt["Sum"] == expected_sum, "Source graph collection evidence differs")
    if not expected_sum:
        require(receipt["Origin"]["Hash"] == expected_commit, "Source graph collection evidence differs")
    source = OUT / "sources" / stem
    source.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(receipt["Zip"]) as archive:
        prefix = receipt["Path"] + "@" + receipt["Version"] + "/"
        for name in archive.namelist():
            require(name.startswith(prefix), "Source graph collection evidence differs")
            relative = Path(name[len(prefix) :])
            require(".." not in relative.parts, "Source graph collection evidence differs")
            if name.endswith("/"):
                continue
            dest = source / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(name))
    return (source, {k: receipt.get(k) for k in ["Path", "Version", "Sum", "GoModSum", "Origin"]})


def prepare(stem):
    component = {"am": "alertmanager", "otelcol": "otel-collector"}.get(stem, stem)
    vendor = ROOT / "deployment/vendors" / component
    lock = json.loads((vendor / "source.lock.json").read_bytes())
    tool = "go1.27.4"
    if stem == "etcd":
        import urllib.request

        archive = OUT / "etcd-source.tar.gz"
        request = urllib.request.Request(
            "https://codeload.github.com/etcd-io/etcd/tar.gz/" + lock["commit"],
            headers={"User-Agent": "DeepResearch-source-review"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            archive.write_bytes(response.read())
        require(sha(archive) == lock["source_archive_sha256"], "Source graph collection evidence differs")
        source = OUT / "sources" / stem
        source.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as src:
            for member in src:
                relative = Path(*Path(member.name).parts[1:])
                require(".." not in relative.parts, "Source graph collection evidence differs")
                if not member.isfile():
                    continue
                dest = source / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.extractfile(member).read())
        provenance = {"archive_sha256": sha(archive), "commit": lock["commit"]}
    elif stem in ["am", "loki", "prometheus", "tempo"]:
        module = {
            "am": "github.com/prometheus/alertmanager",
            "loki": "github.com/grafana/loki/v3",
            "prometheus": "github.com/prometheus/prometheus",
            "tempo": "github.com/grafana/tempo",
        }[stem]
        version = {"am": "v0.34.1", "loki": "v3.7.9", "prometheus": "v0.315.1", "tempo": lock["commit"]}[stem]
        source, provenance = module_source(
            stem, module, version, lock.get("module_sum"), lock["commit"], tool
        )
    else:
        source = OUT / "sources" / stem
        source.mkdir(parents=True, exist_ok=True)
        provenance = {"kind": "entirely_tracked_generated_distribution"}
    inputs = {}
    if stem == "etcd":
        overlay = vendor / "modules"
        for path in overlay.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                dest = source / path.relative_to(overlay)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                inputs[path.relative_to(ROOT).as_posix()] = sha(path)
    elif stem == "otelcol":
        for path in (vendor / "source").glob("*"):
            if path.is_file():
                shutil.copyfile(path, source / path.name)
                inputs[path.relative_to(ROOT).as_posix()] = sha(path)
    else:
        for name in ["go.mod", "go.sum"]:
            path = vendor / name
            shutil.copyfile(path, source / name)
            inputs[path.relative_to(ROOT).as_posix()] = sha(path)
        if stem == "loki":
            for path in (vendor / "push").glob("*"):
                if path.is_file():
                    dest = source / "pkg/push" / path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, dest)
                    inputs[path.relative_to(ROOT).as_posix()] = sha(path)
    inputs[(vendor / "source.lock.json").relative_to(ROOT).as_posix()] = sha(vendor / "source.lock.json")
    recipe = "deployment/docker/Dockerfile." + component
    inputs[recipe] = sha(ROOT / recipe)
    if stem in ["am", "prometheus"]:
        import urllib.request

        prefix = "official_ui" if stem == "am" else "ui"
        artifact = OUT / (stem + "-ui.tar.gz")
        if not artifact.exists():
            request = urllib.request.Request(
                lock[prefix + "_url"], headers={"User-Agent": "DeepResearch-release-verification"}
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                artifact.write_bytes(response.read())
        require(sha(artifact) == lock[prefix + "_sha256"], "Source graph collection evidence differs")
        target = source / "ui/app" if stem == "am" else source / "web/ui"
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(artifact) as archive:
            archive.extractall(target, filter="data")
        provenance["ui_sha256"] = sha(artifact)
    return (source, inputs, provenance, tool)


def collect(name, cwd, args, inputs, provenance, tool):
    env = os.environ.copy()
    env.update(
        GOOS="linux",
        GOARCH="amd64",
        GOAMD64="v1",
        CGO_ENABLED="0",
        GOWORK="off",
        GOTOOLCHAIN=tool,
        GOMAXPROCS="2",
        GOMEMLIMIT="1024MiB",
        GOPROXY="https://proxy.golang.org",
        GOSUMDB="sum.golang.org",
    )
    command = [GO, "list", "-deps", "-json", "-mod=readonly", "-buildvcs=false", *args]
    raw = checked_run(command, cwd, env, name)
    packages = decode_graph(raw)
    for row in packages.values():
        require(
            not row.get("Error") and (not row.get("Incomplete")) and (not row.get("DepsErrors")),
            "Source graph collection evidence differs",
        )
        require(
            set(row.get("Imports", [])) - {"C"} <= packages.keys(), "Source graph collection evidence differs"
        )
    require(
        not any(
            (
                p == "golang.org/x/crypto/openpgp" or p.startswith("golang.org/x/crypto/openpgp/")
                for p in packages
            )
        ),
        "Source graph collection evidence differs",
    )
    public = []
    for row in packages.values():
        value = {
            k: row[k]
            for k in ["ImportPath", "Name", "Imports", "Deps", "ImportMap", "Standard", "ForTest", "DepOnly", "Match"]
            if k in row
        }

        def mod(m):
            value = {k: m[k] for k in ["Path", "Version", "Sum", "GoModSum", "Main", "GoVersion"] if k in m}
            if "Replace" in m:
                value["Replace"] = mod(m["Replace"])
            return value

        if "Module" in row:
            value["Module"] = mod(row["Module"])
        public.append(value)
    target = OUT / (name + ".json.gz")
    payload = json.dumps(
        {"schema": "current-source-context-20261115-v1", "packages": public, "package_count": len(public)},
        separators=(",", ":"),
    ).encode()
    target.write_bytes(gzip.compress(payload, mtime=0))
    receipt = {
        "profile": name,
        "source_inputs": inputs,
        "source_provenance": provenance,
        "command": ["go", *map(str, command[1:])],
        "environment": {
            k: env[k] for k in ["GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED", "GOWORK", "GOTOOLCHAIN"]
        },
        "compiler": subprocess.check_output([str(GO), "version"], env=env).decode().strip(),
        "exit_code": 0,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "projection_sha256": sha(target),
        "projection_raw_sha256": hashlib.sha256(payload).hexdigest(),
        "package_count": len(public),
        "raw_graph_modified": False,
        "projection_preserves_all_packages_imports_and_module_identities": True,
        "projection_entry_metadata_preserved": True,
    }
    write(OUT / (name + ".receipt.json"), receipt)
    print(name, len(public), flush=True)


def group(stem):
    source, inputs, provenance, tool = prepare(stem)
    profiles = {
        "am": [("alertmanager", ".", ["./cmd/alertmanager"]), ("amtool", ".", ["./cmd/amtool"])],
        "loki": [("loki", ".", ["-tags=netgo", "./cmd/loki"])],
        "prometheus": [
            ("prometheus", ".", ["-tags=netgo,builtinassets", "./cmd/prometheus"]),
            ("promtool", ".", ["-tags=netgo,builtinassets", "./cmd/promtool"]),
        ],
        "tempo": [("tempo", ".", ["./cmd/tempo"])],
        "otelcol": [("otel", ".", ["-tags=grpcnotrace", "."])],
        "etcd": [("etcd", "server", ["."]), ("etcdctl", "etcdctl", ["."]), ("etcdutl", "etcdutl", ["."])],
    }[stem]
    for name, relative, args in profiles:
        collect(name, source / relative, args, inputs, provenance, tool)
    if stem == "etcd":
        old = json.loads(
            (
                ROOT / "deployment/security/code-scanning-review-20261110-evidence/standalone/review.json"
            ).read_bytes()
        )
        for row in old["rows"]:
            if not row["target"].startswith("deployment/vendors/etcd/"):
                continue
            relative = Path(row["target"]).parent.relative_to("deployment/vendors/etcd/modules")
            collect(
                "standalone-" + str(row["alert"]),
                source / relative,
                ["-test", *row["command"][6:]],
                inputs,
                provenance,
                tool,
            )
    return stem


def main(argv=None):
    global GO, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--components",
        nargs="+",
        choices=["am", "etcd", "loki", "prometheus", "tempo", "otelcol"],
        required=True,
    )
    args = parser.parse_args(argv)
    GO = args.go.resolve()
    OUT = args.output.resolve()
    if OUT.exists() or not OUT.is_relative_to((ROOT / ".cache").resolve()):
        raise ValueError("Use a new output under this checkout cache")
    OUT.mkdir(parents=True)
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        tasks = {pool.submit(group, n): n for n in args.components}
        for task in concurrent.futures.as_completed(tasks):
            try:
                print(
                    json.dumps(
                        {
                            "component": task.result(),
                            "source_graphs_completed": True,
                            "native_runtime_verified": False,
                        }
                    ),
                    flush=True,
                )
            except Exception as error:
                failed.append(tasks[task])
                print(
                    json.dumps(
                        {"component": tasks[task], "source_graphs_completed": False, "error": str(error)}
                    ),
                    flush=True,
                )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
