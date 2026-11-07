"""Candidate backend build: retain source h1 and verify the actual Git revision."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def run(command, cwd, timeout=900):
    result = subprocess.run(command, cwd=cwd, capture_output=True,
                            text=True, encoding="utf-8", timeout=timeout)
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.stdout


def validate_nested_module(module):
    nested = module.get("nested_modules", [])
    if module["plugin_directory"] == "prometheus":
        if (module["module"] != "github.com/grafana/grafana-prometheus-datasource"
                or module["commit"] != "7d5bbc86bd38f95ca5143c8490d1204787cc391b"
                or nested != ["pkg/promlib"]):
            raise ValueError("The pinned Prometheus module requires its exact pkg/promlib Git subtree")
    elif nested:
        raise ValueError("Nested modules are forbidden for every other plugin")
    return nested


def validate_subtree(identity):
    fields = identity.split()
    if len(fields) != 4 or fields[0:2] != ["040000", "tree"] or fields[3] != "pkg/promlib":
        raise ValueError("Pinned Prometheus nested module is not the expected real Git tree")
    if len(fields[2]) != 40 or any(char not in "0123456789abcdef" for char in fields[2]):
        raise ValueError("Pinned nested Git tree object identity is invalid")


def validate_native_local_dependencies(module, native, nested):
    local = {dep["Path"]: dep["Replace"]["Path"] for dep in native.get("Deps", [])
             if dep.get("Replace") and dep["Replace"].get("Version") in (None, "", "(devel)")}
    expected_local = {module["module"] + "/" + relative: "./" + relative for relative in nested}
    if local != expected_local or any(dep.get("Version") in (None, "", "(devel)") and not dep.get("Replace") for dep in native.get("Deps", [])):
        raise ValueError("Unexpected unversioned/local dependency in rebuilt executable")


def build(inventory, locks, output, vcs_cache=None):
    modules = json.loads(inventory.read_text(encoding="utf-8"))
    if {row["plugin_directory"] for row in modules} != {'mysql', 'stackdriver', 'mssql', 'jaeger', 'influxdb', 'loki', 'opentsdb', 'tempo', 'elasticsearch', 'grafana-postgresql-datasource', 'prometheus', 'zipkin', 'grafana-pyroscope-datasource'} or len(modules) != 13:
        raise ValueError("Unexpected reviewed backend inventory")
    os.environ["GOWORK"] = "off"
    for module in modules:
        nested = validate_nested_module(module)
        directory, name = module["plugin_directory"], module["binary"]
        source = Path("/source-plugins") / directory
        source.mkdir(parents=True, exist_ok=False)
        metadata = json.loads(run(["go", "mod", "download", "-json",
                                   module["module"] + "@" + module["version"]], Path("/tmp")))
        origin = metadata.get("Origin")
        if (metadata.get("Sum") != module["source_sum"]
                or metadata.get("GoModSum") != module["source_mod_sum"]
                or (origin is not None and (not isinstance(origin, dict) or origin.get("Hash") != module["commit"]))):
            raise ValueError("Source Go checksum/VCS identity mismatch")
        # Some authentic module proxies omit Origin. Never invent that metadata:
        # the complete archive h1 and actual pinned Git fetch/reset below remain required.
        shutil.copytree(metadata["Dir"], source, dirs_exist_ok=True)
        if vcs_cache:
            git_dir = source / ".git"
            git_dir.mkdir()
            with tarfile.open(vcs_cache / (directory + ".tgz"), "r:gz") as archive:
                archive.extractall(git_dir, filter="data")
            run(["git", "--git-dir=.git", "config", "core.bare", "false"], source)
            run(["git", "--git-dir=.git", "config", "core.filemode", "true"], source)
        else:
            run(["git", "init"], source)
            run(["git", "remote", "add", "origin", "https://" + module["module"] + ".git"], source)
            run(["git", "-c", "http.version=HTTP/1.1", "-c", "http.lowSpeedTime=60",
                 "-c", "http.lowSpeedLimit=1024", "fetch", "--depth=1", "--no-tags", "origin", module["commit"]], source, timeout=180)
        run(["git", "--git-dir=.git", "config", "core.hooksPath", "/dev/null"], source)
        run(["git", "reset", "--mixed", module["commit"]], source)
        actual_revision = run(["git", "rev-parse", "HEAD"], source).strip()
        if actual_revision != module["commit"]:
            raise ValueError("Pinned upstream commit not checked out")
        for relative in nested:
            run(["git", "checkout", "HEAD", "--", relative], source)
            identity = run(["git", "ls-tree", "HEAD", relative], source).strip()
            validate_subtree(identity)
        provenance = output / "source-provenance"
        provenance.mkdir(parents=True, exist_ok=True)
        (provenance / (directory + ".json")).write_text(json.dumps({
            "module": module["module"], "version": module["version"],
            "archive_h1": metadata["Sum"], "expected_archive_h1": module["source_sum"],
            "actual_proxy_origin": origin, "actual_git_revision": actual_revision,
            "git_revision_verified_after_fetch_and_reset": True,
            "origin_metadata_invented": False,
            "nested_git_subtrees": {relative: run(["git", "ls-tree", "HEAD", relative], source).strip() for relative in nested},
            "boundary": "Source transport/provenance only; not a security disposition or approval."
        }, indent=2) + "\n", encoding="utf-8")
        for lock in (locks / directory).rglob("*"):
            if lock.is_file():
                destination_lock = source / lock.relative_to(locks / directory)
                destination_lock.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(lock, destination_lock)
        destination = output / "plugins" / directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        print("Building reviewed backend " + directory, flush=True)
        run(["go", "build", "-mod=readonly", "-buildvcs=true", "-trimpath",
             "-tags=arrow_json_stdlib", "-ldflags=-s -w -X main.pluginID=" + directory + " -X main.version=" + module["plugin_version"], "-o", str(destination), module["source_package"]], source)
        information = run(["go", "version", "-m", str(destination)], source)
        if "vcs.revision=" + module["commit"] not in information:
            raise ValueError("Actual upstream VCS/module version missing from rebuilt executable")
        native = json.loads(run(["go", "version", "-m", "-json", str(destination)], source))
        validate_native_local_dependencies(module, native, nested)
        main_version = next(line.split()[2] for line in information.splitlines() if line.strip().startswith("mod\t"))
        if main_version not in (module["version"], module["version"] + "+dirty"):
            raise ValueError("Unexpected rebuilt main module version: " + main_version)
        info_dir = output / "build-info"
        info_dir.mkdir(exist_ok=True)
        (info_dir / (directory + ".txt")).write_text(information, encoding="utf-8")
        dependencies = run(["go", "list", "-deps", "-mod=readonly", "-f", "{{.ImportPath}}", module["source_package"]], source)
        (info_dir / (directory + ".packages.txt")).write_text(dependencies, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inventory", "locks", "output", "vcs-cache"):
        parser.add_argument("--" + name, type=Path, required=name != "vcs-cache")
    args = parser.parse_args()
    build(args.inventory, args.locks, args.output, args.vcs_cache)
