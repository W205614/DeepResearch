"""Build reviewed upstream plugin sources with fixed locks and real VCS metadata."""
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


def build(inventory, locks, output, vcs_cache=None):
    modules = json.loads(inventory.read_text(encoding="utf-8"))
    if len(modules) != 8:
        raise ValueError("Unexpected reviewed backend inventory")
    os.environ["GOWORK"] = "off"
    for module in modules:
        directory, name = module["plugin_directory"], module["binary"]
        source = Path("/source-plugins") / directory
        source.mkdir(parents=True, exist_ok=False)
        metadata = json.loads(run(["go", "mod", "download", "-json",
                                   module["module"] + "@" + module["version"]], Path("/tmp")))
        if metadata.get("Sum") != module["source_sum"] or metadata["Origin"]["Hash"] != module["commit"]:
            raise ValueError("Source Go checksum/VCS identity mismatch")
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
        if run(["git", "rev-parse", "HEAD"], source).strip() != module["commit"]:
            raise ValueError("Pinned upstream commit not checked out")
        for filename in ("go.mod", "go.sum"):
            shutil.copyfile(locks / directory / filename, source / filename)
        destination = output / "plugins" / directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        print("Building reviewed backend " + directory, flush=True)
        run(["go", "build", "-mod=readonly", "-buildvcs=true", "-trimpath",
             "-ldflags=-s -w", "-o", str(destination), module["source_package"]], source)
        information = run(["go", "version", "-m", str(destination)], source)
        if "vcs.revision=" + module["commit"] not in information or "(devel)" in information:
            raise ValueError("Actual upstream VCS/module version missing from rebuilt executable")
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
