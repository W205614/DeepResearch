"""Collect pinned official source/package facts; never grant a CVE disposition."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import zipfile

ROOT = Path(__file__).resolve().parents[3]
NAMES = {"tempo", "prometheus", "zipkin"}


class MaterialError(ValueError):
    pass


def require(value, message):
    if not value:
        raise MaterialError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repository_file(name):
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in name, "Unsafe input path")
    path = ROOT / relative
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(ROOT.resolve()), "Missing input file")
    return path


def decode_stream(raw):
    text, offset, values = raw.decode("utf-8"), 0, []
    decoder = json.JSONDecoder()
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        value, offset = decoder.raw_decode(text, offset)
        require(type(value) is dict, "Package stream contains a non-object")
        values.append(value)
    require(bool(values), "Package stream is empty")
    return values


def compare_graph(records, native, entry, local_replacements, source):
    """Compare every effective dependency; local replacements keep their actual identity."""
    paths = [row.get("ImportPath") for row in records]
    require(all(type(name) is str and name for name in paths) and len(paths) == len(set(paths)),
            "Package identities are missing or duplicated")
    require(all(not row.get("Error") and not row.get("DepsErrors") and not row.get("Incomplete")
                and not row.get("ForTest") for row in records), "Incomplete or test-profile package graph")
    require(entry["module"] + "/pkg" in paths, "Expected production entry package is missing")
    modules = {}
    for row in records:
        module = row.get("Module")
        if module:
            previous = modules.setdefault(module["Path"], module)
            require(previous == module, "Conflicting package-to-module metadata")
    main = native["Main"]
    dependencies = {module["Path"]: module for module in native.get("Deps", [])}
    require(len(dependencies) == len(native.get("Deps", [])), "Native module identities are duplicated")
    require(set(modules) == set(dependencies) | {main["Path"]}, "Complete source/native module inventories differ")
    require(modules[main["Path"]].get("Main") is True, "Main source module was not selected")
    local = {row["module"]: row for row in local_replacements}
    local_results = []
    for name, actual in dependencies.items():
        module = modules[name]
        effective, compiled = module.get("Replace") or module, actual.get("Replace") or actual
        if name in local:
            item = local[name]
            expected = (source / item["path"]).resolve()
            require(module.get("Replace") and actual.get("Replace"), "Expected local replacement is missing")
            require(effective["Path"] == compiled["Path"] == "./" + item["path"],
                    "Native/source local replacement path differs")
            require((module.get("Version") or "") == (actual.get("Version") or ""),
                    "Local replacement declared version differs")
            require(effective.get("Version", "") in {"", "(devel)"}
                    and compiled.get("Version", "") in {"", "(devel)"}
                    and not effective.get("Sum") and not compiled.get("Sum"),
                    "Unexpected local replacement version/checksum")
            require(Path(effective["Dir"]).resolve() == expected, "Local replacement resolves outside its exact source")
            local_results.append({"module": name, "path": item["path"],
                                  "source_commit": item["source_commit"],
                                  "native_replace": compiled, "graph_replace": effective,
                                  "local_go_mod_sha256": digest(expected / "go.mod")})
        else:
            for key in ("Path", "Version", "Sum"):
                require((effective.get(key) or "") == (compiled.get(key) or ""),
                        "Effective dependency " + name + " differs at " + key)
    return {"package_count": len(paths), "module_count_including_main": len(modules),
            "native_dependency_count": len(dependencies), "complete_effective_dependencies_equal": True,
            "local_replacements": local_results,
            "html_packages": sorted(name for name in paths if name == "golang.org/x/net/html"
                                    or name.startswith("golang.org/x/net/html/"))}


class Runner:
    def __init__(self, directory, environment, timeout):
        self.directory, self.environment, self.timeout = directory, environment, timeout
        self.commands = []

    def run(self, label, command, cwd, check=True):
        start = time.monotonic()
        try:
            result = subprocess.run(command, cwd=cwd, env=self.environment, capture_output=True,
                                    timeout=self.timeout, check=False)
            stdout, stderr, status = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr, status = error.stdout or b"", error.stderr or b"", "timeout"
        (self.directory / (label + ".stdout")).write_bytes(stdout)
        (self.directory / (label + ".stderr")).write_bytes(stderr)
        self.commands.append({"label": label, "command": command, "cwd": str(cwd), "status": status,
                              "duration_seconds": round(time.monotonic() - start, 3)})
        if check:
            require(status == 0, label + " failed; raw stdout/stderr retained")
        return stdout, stderr, status

    def git(self, label, arguments, cwd):
        return self.run(label, ["git", "-c", "http.sslVerify=true", "-c", "core.autocrlf=false",
                                "-c", "core.hooksPath=/dev/null", *arguments], cwd)[0]


def compare_module_zip(zip_path, source, module, version):
    count = 0
    prefix = module + "@" + version + "/"
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            require(member.filename.startswith(prefix), "Module ZIP prefix differs")
            relative = PurePosixPath(member.filename[len(prefix):])
            require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe module member")
            path = source / relative
            require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(source.resolve()),
                    "Git checkout omits module ZIP member")
            require(path.read_bytes() == archive.read(member), "Git/module ZIP source bytes differ: " + relative.as_posix())
            count += 1
    require(count > 0, "Module ZIP source inventory is empty")
    return count


def capture(entry, lock, output, environment, go, timeout):
    directory = output / entry["name"]
    directory.mkdir()
    runner = Runner(directory, environment, timeout)
    summary = {"name": entry["name"], "status": "unverified", "new_candidate_approved": False,
               "CVE_disposition": "NOT_DECIDED", "input_native_ELF_sha256": entry["elf_sha256"],
               "native_ELF_bytes_read_during_this_collection": False}
    source = directory / "source"
    original_mods = {}
    try:
        path = repository_file(entry["native_buildinfo"]["path"])
        require(digest(path) == entry["native_buildinfo"]["sha256"], "Native input bytes changed")
        native = json.loads(path.read_bytes())
        settings = {row["Key"]: row["Value"] for row in native["Settings"]}
        require(native["GoVersion"] == lock["profile"]["go_version"]
                and native["Main"]["Path"] == entry["module"]
                and native["Main"]["Version"] == entry["version"]
                and settings["vcs.revision"] == entry["source_commit"]
                and settings["vcs.modified"] == "false", "Native source identity is not the exact clean revision")
        for name in ("GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED"):
            require(settings.get(name) == lock["profile"][name], "Native build setting differs: " + name)
        require(settings.get("-tags", "").split(",") == lock["profile"]["tags"]
                and settings.get("-buildmode") == "exe", "Native compilation profile differs")
        raw, _, _ = runner.run("source-module", [go, "mod", "download", "-json",
                                               entry["module"] + "@" + entry["version"]], directory)
        metadata = json.loads(raw)
        require(metadata.get("Path") == entry["module"] and metadata.get("Version") == entry["version"]
                and metadata.get("Sum") == entry["module_h1"] and metadata.get("GoModSum") == entry["go_mod_h1"]
                and metadata.get("Origin", {}).get("Hash") == entry["source_commit"],
                "Official module checksum or Git origin differs")
        source.mkdir()
        runner.git("git-init", ["init"], source)
        runner.git("git-origin", ["remote", "add", "origin", entry["source_repository"]], source)
        runner.git("git-fetch", ["fetch", "--depth=1", "--no-tags", "origin", entry["source_commit"]], source)
        runner.git("git-checkout", ["checkout", "--detach", "FETCH_HEAD"], source)
        revision = runner.git("git-revision", ["rev-parse", "HEAD"], source).decode().strip()
        require(revision == entry["source_commit"], "Checked-out Git revision differs")
        require(not runner.git("git-initial-status", ["status", "--porcelain", "--untracked-files=no"], source).strip(),
                "Initial Git checkout is dirty")
        require((source / "go.mod").read_bytes() == Path(metadata["GoMod"]).read_bytes(), "Root Go module bytes differ")
        zip_members = compare_module_zip(Path(metadata["Zip"]), source, entry["module"], entry["version"])
        for replacement in entry["local_replacements"]:
            require(replacement["source_commit"] == revision, "Local replacement has another Git source")
            relative = PurePosixPath(replacement["path"])
            require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe local replacement path")
            local = source / relative
            require(local.is_dir() and not local.is_symlink() and local.resolve().is_relative_to(source.resolve()),
                    "Exact Git checkout lacks the complete local replacement")
            require(re.search(r"(?m)^module\s+" + re.escape(replacement["module"]) + r"\s*$",
                              (local / "go.mod").read_text(encoding="utf-8")), "Local module directive differs")
            tree = runner.git("local-tree-" + relative.name, ["rev-parse", "HEAD:" + relative.as_posix()], source).decode().strip()
            require(bool(re.fullmatch("[0-9a-f]{40}", tree)), "Local module Git tree identity missing")
        tracked = runner.git("source-lock-inventory", ["ls-files", "--", "go.mod", "go.sum", "**/go.mod", "**/go.sum"], source).decode().splitlines()
        locks = directory / "locks"
        for name in tracked:
            path = source / name
            if path.name == "go.mod":
                original_mods[name] = digest(path)
            destination = locks / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        command = [go, "list", "-deps", "-mod=readonly", "-tags=" + ",".join(lock["profile"]["tags"]),
                   "-json", entry["source_package"]]
        graph, error, status = runner.run("production-graph-1", command, source, check=False)
        if status != 0 and b"missing go.sum entry" in error:
            runner.run("default-sumdb-download", [go, "mod", "download"], source)
            require(all(digest(source / name) == value for name, value in original_mods.items()),
                    "Default download modified a source go.mod")
            graph, error, status = runner.run("production-graph-2", command, source, check=False)
        require(status == 0, "Complete production graph unavailable; failure output retained")
        require(all(digest(source / name) == value for name, value in original_mods.items()),
                "Source go.mod changed during collection")
        facts = compare_graph(decode_stream(graph), native, entry, entry["local_replacements"], source)
        (directory / "production-graph-complete.json").write_bytes(graph)
        for name in tracked:
            path = source / name
            destination = directory / "resolved-locks" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        final_status = runner.git("git-final-status", ["status", "--porcelain", "--untracked-files=no"], source).decode()
        for line in final_status.splitlines():
            require(line[3:].endswith("go.sum"), "Source file changed beyond allowed go.sum: " + line[3:])
        summary.update(status="materials_consistent", source_commit=revision,
                       source_module_h1=metadata["Sum"], source_go_mod_h1=metadata["GoModSum"],
                       git_module_zip_members_compared=zip_members,
                       module_zip_sha256=digest(Path(metadata["Zip"])),
                       complete_graph_sha256=hashlib.sha256(graph).hexdigest(),
                       native_buildinfo_sha256=entry["native_buildinfo"]["sha256"], facts=facts,
                       source_go_mod_sha256_before_after=original_mods,
                       allowed_go_sum_changes=final_status.splitlines())
    except (MaterialError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        summary["error"] = str(error)
        summary["source_go_mods_still_unchanged"] = all((source / name).is_file()
                                                      and digest(source / name) == value
                                                      for name, value in original_mods.items())
    finally:
        (directory / "provenance.json").write_text(json.dumps({"commands": runner.commands,
            "environment": {name: environment[name] for name in ("GOTOOLCHAIN", "GOWORK", "GOPROXY", "GOSUMDB",
                "GOPRIVATE", "GONOPROXY", "GONOSUMDB", "GOINSECURE", "GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED")}},
            indent=2) + "\n", encoding="utf-8")
        (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("official-profiles.lock.json"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--go", default="go")
    parser.add_argument("--only", choices=sorted(NAMES))
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    lock = json.loads(args.lock.read_bytes())
    require(lock["schema"] == "grafana-official-production-profile-inputs-v1", "Unknown input lock format")
    require({row["name"] for row in lock["profiles"]} == NAMES and len(lock["profiles"]) == 3,
            "Exact three official profiles are required")
    require(args.timeout_seconds > 0, "A positive command timeout is required")
    output = args.output.resolve()
    require(output.is_relative_to((ROOT / ".cache").resolve()) and not output.exists(),
            "Use a new output directory inside this checkout .cache")
    output.mkdir(parents=True)
    environment = {**os.environ, "GOTOOLCHAIN": "local", "GOWORK": "off",
        "GOPROXY": "https://proxy.golang.org", "GOSUMDB": "sum.golang.org",
        "GOPRIVATE": "", "GONOPROXY": "", "GONOSUMDB": "", "GOINSECURE": "",
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_SSL_NO_VERIFY": "false", "GOMODCACHE": str(output / "gomodcache"),
        "GOCACHE": str(output / "gocache"), "GOFLAGS": "-p=2", "GOMAXPROCS": "2",
        **{name: lock["profile"][name] for name in ("GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED")}}
    runtime = Runner(output, environment, args.timeout_seconds)
    try:
        raw, _, _ = runtime.run("go-runtime", [args.go, "env", "-json", "GOVERSION", "GOHOSTOS", "GOHOSTARCH"], ROOT)
        actual = json.loads(raw)
        require(actual == {"GOVERSION": lock["profile"]["go_version"], "GOHOSTOS": "linux", "GOHOSTARCH": "amd64"},
                "Official profile collection requires the exact Go1.27.3 linux/amd64 toolchain")
    except (MaterialError, OSError, ValueError, KeyError, TypeError) as error:
        failure = {"status": "unverified", "all_selected_materials_consistent": False,
                   "CVE_disposition": "NOT_DECIDED", "error": str(error), "commands": runtime.commands}
        (output / "summary.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure))
        return 1
    reports = []
    for entry in lock["profiles"]:
        require(entry["source_repository"] == "https://" + entry["module"] + ".git"
                and entry["module"].startswith("github.com/grafana/")
                and re.fullmatch("[0-9a-f]{40}", entry["source_commit"]), "Unsafe or unpinned source identity")
        if not args.only or entry["name"] == args.only:
            reports.append(capture(entry, lock, output, environment, args.go, args.timeout_seconds))
    summary = {"schema": "grafana-official-production-materials-v1", "input_lock_sha256": digest(args.lock),
               "original_subject": lock["original_subject"], "new_candidate_approved": False,
               "CVE_disposition": "NOT_DECIDED", "native_inputs_apply_to_new_candidate": False,
               "all_selected_materials_consistent": bool(reports) and all(row["status"] == "materials_consistent" for row in reports),
               "profiles": reports}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_selected_materials_consistent": summary["all_selected_materials_consistent"],
                      "CVE_disposition": "NOT_DECIDED", "output": str(output)}))
    return 0 if summary["all_selected_materials_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
