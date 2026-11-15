"""Produce a separately reviewed dependency continuation; never change the default or budget."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_ci_evaluation_budget as precheck  # noqa: E402

BASE = "3a3b502c2b3a95ad59b69b1279591c08c8d2022e"
PRIOR = "eval/ci-budget-source-basis.json"
SDIST_SHA256 = "ca9abf7126667010d8f6e9b39bfc1bc3b23c8aed9bcb0f54a754dde969bb8aab"
WHEEL_SHA256 = "e4d667b3974ce6e038e7390486b8093369854a1d345ffdd4c121dfc4a782e809"
ALLOWED_IMPORTS = {
    "__future__",
    "bisect",
    "bs4",
    "collections",
    "copyreg",
    "dataclasses",
    "datetime",
    "enum",
    "functools",
    "math",
    "re",
    "threading",
    "typing",
    "warnings",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(name):
    return subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)


def normalize(value):
    if isinstance(value, dict):
        return {
            key: sorted(item) if key == "resolution-markers" else normalize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def dependency_delta(prior):
    previous = {name: blob(name) for name in ("pyproject.toml", "uv.lock")}
    current = {name: (ROOT / name).read_bytes() for name in previous}
    for name in previous:
        assert sha(previous[name]) == prior["case_fingerprints"][name]
    old_project, new_project = [
        tomllib.loads(raw.decode()) for raw in (previous["pyproject.toml"], current["pyproject.toml"])
    ]
    assert old_project["project"]["requires-python"] == ">=3.11,<3.14"
    assert new_project["project"]["requires-python"] == ">=3.11.5,<3.14"
    new_project["project"]["requires-python"] = old_project["project"]["requires-python"]
    assert new_project == old_project, "The Python support floor must be the only pyproject delta"
    old_lock, new_lock = [tomllib.loads(raw.decode()) for raw in (previous["uv.lock"], current["uv.lock"])]
    assert old_lock["requires-python"] == ">=3.11, <3.14" and new_lock["requires-python"] == ">=3.11.5, <3.14"
    new_lock["requires-python"] = old_lock["requires-python"]
    old_packages, new_packages = [
        {row["name"]: row for row in lock.pop("package")} for lock in (old_lock, new_lock)
    ]
    assert normalize(old_lock) == normalize(new_lock), "Other lock header semantics changed"
    old_soup, new_soup = old_packages.pop("soupsieve"), new_packages.pop("soupsieve")
    assert old_soup["version"] == "2.9.2" and new_soup["version"] == "4.1"
    assert new_soup["sdist"]["hash"] == "sha256:" + SDIST_SHA256
    assert len(new_soup["wheels"]) == 1 and new_soup["wheels"][0]["hash"] == "sha256:" + WHEEL_SHA256
    assert old_soup["source"] == new_soup["source"] == {"registry": "https://pypi.org/simple"}
    removed = old_packages.pop("async-timeout")
    assert removed["version"] == "5.0.1" and "async-timeout" not in new_packages
    old_redis, new_redis = old_packages["redis"], new_packages["redis"]
    obsolete = {"name": "async-timeout", "marker": "python_full_version < '3.11.3'"}
    assert old_redis["dependencies"].count(obsolete) == 1
    old_redis["dependencies"].remove(obsolete)
    assert old_redis == new_redis
    assert normalize(old_packages) == normalize(new_packages), (
        "Any further package identity, artifact or dependency change requires re-audit"
    )
    return {
        "files": {
            name: {"prior_sha256": sha(previous[name]), "current_sha256": sha(current[name])}
            for name in previous
        },
        "soupsieve": {"prior": old_soup, "current": new_soup},
        "python_floor": {"prior": ">=3.11,<3.14", "current": ">=3.11.5,<3.14"},
        "removed_dependency": {
            "package": removed,
            "sole_edge": obsolete,
            "reason": "Its marker is false throughout the new supported Python range (>=3.11.5).",
        },
        "other_package_records_identical_after_resolution_marker_order_normalization": len(new_packages),
        "resolution_marker_order_only_is_ignored": True,
    }


def audit_soupsieve(sdist, wheel, delta):
    assert sha(sdist.read_bytes()) == SDIST_SHA256 and sdist.stat().st_size == 161955
    assert sha(wheel.read_bytes()) == WHEEL_SHA256 and wheel.stat().st_size == 52302
    imports, files = {}, {}
    with tarfile.open(sdist) as source, zipfile.ZipFile(wheel) as package:
        names = [
            name for name in package.namelist() if name.startswith("soupsieve/") and not name.endswith("/")
        ]
        assert len(names) == 9
        for name in names:
            body = package.read(name)
            assert body == source.extractfile("soupsieve-4.1/" + name).read()
            files[name] = sha(body)
            if not name.endswith(".py"):
                continue
            tree = ast.parse(body)
            observed = {
                item.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for item in node.names
            }
            observed |= {
                node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
            }
            assert observed <= ALLOWED_IMPORTS, (
                "An additional package import requires explicit HTTP-path review"
            )
            assert not any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec", "__import__"}
                for node in ast.walk(tree)
            )
            imports[name] = sorted(observed)
        license_body = source.extractfile("soupsieve-4.1/LICENSE.md").read()
        assert license_body == package.read("soupsieve-4.1.dist-info/licenses/LICENSE.md")
    return {
        "official_sdist": delta["soupsieve"]["current"]["sdist"],
        "official_wheel": delta["soupsieve"]["current"]["wheels"][0],
        "official_source_and_locked_wheel_package_file_bytes_identical": files,
        "external_import_roots_by_python_file": imports,
        "dynamic_eval_exec_or_import_calls": [],
        "license_sha256": sha(license_body),
        "network_imports_or_supplier_http_entry_added": False,
        "scope": "All eight official SoupSieve Python source files import only the listed standard-library roots and unchanged bs4. No HTTP/socket/client import or dynamic eval/exec/import entry exists. This is a static package/path review, not a new paid quality result.",
    }


def generate(sdist, wheel, output):
    assert not output.exists(), "Keep previous review outputs; use a new output path"
    old_raw = (ROOT / PRIOR).read_bytes()
    assert old_raw == blob(PRIOR), "The historical basis must remain exact"
    prior = json.loads(old_raw)
    failures, references = precheck.source_basis(ROOT, prior)
    assert not failures and len(references) == 37
    delta = dependency_delta(prior)
    for name, expected in prior["case_fingerprints"].items():
        if name not in delta["files"]:
            assert precheck.fingerprint(ROOT / name) == expected
    official = audit_soupsieve(sdist, wheel, delta)
    new = deepcopy(prior)
    new["case_fingerprints"].update({name: row["current_sha256"] for name, row in delta["files"].items()})
    new["source_reaudit_20261115"] = {
        "prior_basis": {
            "path": PRIOR,
            "sha256": sha(old_raw),
            "source_commit": BASE,
            "bytes_preserved": True,
        },
        "actor_kind": "automated_static_technical_reaudit",
        "producer": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "sha256": sha(Path(__file__).read_bytes()),
        },
        "dependency_delta": delta,
        "official_soupsieve_source_review": official,
        "unchanged_audited_symbols": references,
        "unchanged_fresh_invocations": prior["fresh_invocations"],
        "proof_review": [
            "All 37 audited graph/provider/grading/runner symbols and all four paid workflow invocations retain their exact audited AST/command identities. All three case datasets retain their exact bytes and therefore case/critical/source/refusal flags.",
            "The Python support floor alone increases to3.11.5; the removed async-timeout edge had marker<3.11.3 and is unreachable throughout this supported range. Other dependency records/artifact identities remain exact except the fixed official SoupSieve4.1 update and irrelevant marker ordering.",
            "All eight official SoupSieve Python files and py.typed exactly match the locked official wheel; imports have no network/client module and no dynamic eval/exec/import entry. bs4 and every provider, HTTP dispatch, retry, model validation and external support/coverage symbol remain exact.",
            "A passing positive case still needs the same mandatory planner/judge/analyst/writer/validator/coverage and independent support/coverage grading HTTP calls. No dataset count, required success threshold or refusal condition changes. The original checker recomputes the necessary bounds; this does not establish a sufficient budget or a paid-quality pass.",
        ],
        "supplier_http_requests": 0,
        "policy_authorization_or_ledger_changed": False,
        "production_default_changed_by_generator": False,
    }
    with tempfile.TemporaryDirectory(prefix="budget-static-proof-") as directory:
        temporary = Path(directory)
        basis_path, policy_path = temporary / "basis.json", temporary / "policy.json"
        basis_path.write_text(json.dumps(new), encoding="utf-8")
        policy = json.loads((ROOT / "eval/ci-provider-budget.json").read_bytes())
        policy.pop("ci_series_id", None)
        # Synthetic policy only establishes that the unchanged checker recomputes
        # the proof. It neither writes production policy nor permits a paid call.
        policy.update(model_http=296, llm_http=296, total_http=296)
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        report = precheck.assess(
            basis_path=basis_path, policy_path=policy_path, ledger_path=temporary / "never-created.sqlite3"
        )
        assert (
            report["proof_verified"] and not report["proof_drift"] and report["supplier_http_requests"] == 0
        )
        assert (
            report["critical_path_lower_bound"] == 245 and report["pass_rate_refinement_lower_bound"] == 296
        )
        assert (
            report["required_lower_bound"] == 296
            and report["precheck_status"] == "necessary_bound_not_violated"
        )
        assert not (temporary / "never-created.sqlite3").exists()
        new["source_reaudit_20261115"]["original_checker_recomputed"] = {
            key: report[key]
            for key in (
                "proof_verified",
                "critical_path_lower_bound",
                "pass_rate_refinement_lower_bound",
                "required_lower_bound",
                "supplier_http_requests",
            )
        }
    output.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8", newline="\n")
    assert (ROOT / PRIOR).read_bytes() == old_raw
    print(
        json.dumps(
            {
                "draft": str(output),
                "sha256": sha(output.read_bytes()),
                "production_default_changed": False,
                "supplier_http_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    generate(options.sdist, options.wheel, options.output)
