"""Measure a graph-prompt-only Agent continuation; never publish or call a provider."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_agent_full_scope as gate  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402
from scripts.prepare_agent_soupsieve_20261115 import inventory  # noqa: E402

NAME = "agent-gap-coverage-20261115"
BASE = "sha256:7000327fd7058098d44e559827835ab7248ac9b62165725745537ad63bf03dfb"
BASE_TAG = "dr-v015-agent-soup41-flat:20261115"
BASE_CONFIG = "sha256:02f79004b132d195ac2fb376086156e6495fa24c7dfa6730ff657a1e66858c74"
PRIOR_PROFILE = "deployment/security/agent-soupsieve41-20261115-profile.json"
PRIOR_SHA = "deda4e1f56fc89070525c18e3ad0e433314ea986f8980f0cce24c1a8be99b399"
GRAPH = "backend/research/graph.py"
OLD_GRAPH = "b4bd11568b52059ac0420acedaad5ffb65e22ca6531c15783a5acf39b7d48ce9"
NEW_GRAPH = "71910af11b4e932b5a2d70d739cf71ec3ec433979b2bfbd3bdb341207f1c5994"
TEST = "tests/test_research_gap_coverage.py"
EVIDENCE = ROOT / "deployment/security/agent-gap-coverage-20261115-evidence"
REVIEW = ROOT / "deployment/security/agent-gap-coverage-20261115-review.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    raw = (json.dumps(value, indent=2) + "\n").encode()
    path = Path(path)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Preserve existing evidence: " + str(path))
    path.write_bytes(raw)


def audit_prompt_delta(before, after):
    """Allow exactly one string value in each of two named methods, and nothing else."""
    original, current = ast.parse(before), ast.parse(after)
    old_class = next(n for n in original.body if isinstance(n, ast.ClassDef) and n.name == "ResearchGraph")
    new_class = next(n for n in current.body if isinstance(n, ast.ClassDef) and n.name == "ResearchGraph")
    if len(old_class.body) != len(new_class.body):
        raise ValueError("Graph class structure changed")
    changed = []
    for old, new in zip(old_class.body, new_class.body):
        if ast.dump(old, include_attributes=False) == ast.dump(new, include_attributes=False):
            continue
        if not isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef)) or not isinstance(new, type(old)):
            raise ValueError("Only two prompt methods may change")
        if old.name != new.name or new.name not in {"analyst", "validator"}:
            raise ValueError("Unexpected changed graph method")
        old_strings = [n for n in ast.walk(old) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        new_strings = [n for n in ast.walk(new) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if len(old_strings) != len(new_strings):
            raise ValueError("Prompt structure changed")
        pairs = [(a, b) for a, b in zip(old_strings, new_strings) if a.value != b.value]
        if len(pairs) != 1:
            raise ValueError("Expected one changed prompt string per method")
        pairs[0][1].value = pairs[0][0].value
        if ast.dump(old, include_attributes=False) != ast.dump(new, include_attributes=False):
            raise ValueError("Non-prompt method AST changed")
        changed.append(new.name)
    if changed != ["analyst", "validator"] or ast.dump(original, include_attributes=False) != ast.dump(current, include_attributes=False):
        raise ValueError("Only the two complete-module prompt constants may differ")
    return {"changed_methods": changed, "one_string_constant_each": True, "all_other_complete_module_ast_identical": True}


def checked_prior():
    assert sha(ROOT / PRIOR_PROFILE) == PRIOR_SHA
    profile = read(ROOT / PRIOR_PROFILE)
    review = read(ROOT / profile["source_review_evidence"]["path"])
    assert sha(ROOT / profile["source_review_evidence"]["path"]) == profile["source_review_evidence"]["sha256"]
    for item in review["additional_evidence_files"]:
        if item["path"] != GRAPH:
            assert sha(ROOT / item["path"]) == item["sha256"], item["path"]
        else:
            assert item["sha256"] == OLD_GRAPH
    return profile, review


def candidate(out, tag):
    assert out.is_relative_to((ROOT / ".cache").resolve()) and not out.exists()
    assert sha(ROOT / GRAPH) == NEW_GRAPH
    prior, _ = checked_prior()
    out.mkdir(parents=True)
    base, = read_inspection(BASE)
    alias, = read_inspection(BASE_TAG)
    assert alias["Id"] == base["Id"] == BASE
    base_raw = (ROOT / ".cache/release-v015-20261115/agent-soup41/flatten-probe-01/fullscope-independent-verdict.image-config.raw.json").read_bytes()
    _, digest = gate.verify_actual_config(base_raw, base)
    assert digest == BASE_CONFIG
    (out / "base-image-config.raw.json").write_bytes(base_raw)
    write(out / "base-inspection.json", base)
    before = inventory(BASE, out / "base-runtime-inventory.json")
    assert before["native_elf_closure"] == prior["native_elf_closure"]
    assert before["python_source_closure"] == prior["python_source_closure"]
    before_facts = capture(BASE, out / "base-filesystem")
    old_files = {r["path"]: r for r in before_facts["runtime_manifest"]}
    import tarfile
    with tarfile.open(out / "base-filesystem/rootfs.tar") as archive:
        original_graph = archive.extractfile("app/" + GRAPH).read()
        old_fingerprint = json.load(archive.extractfile("app/source-fingerprint.json"))
    assert hashlib.sha256(original_graph).hexdigest() == OLD_GRAPH
    assert len(old_fingerprint) == 97 and old_fingerprint[GRAPH] == OLD_GRAPH
    delta = audit_prompt_delta(original_graph, (ROOT / GRAPH).read_bytes())
    context = out / "context"
    context.mkdir()
    (context / "graph.py").write_bytes((ROOT / GRAPH).read_bytes())
    graph_file = old_files["app/" + GRAPH]
    regenerate = "import pathlib,hashlib,json; r=pathlib.Path('/app'); m={str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p!=r/'source-fingerprint.json'}; assert len(m)==97; (r/'source-fingerprint.json').write_text(json.dumps(m,sort_keys=True),encoding='utf-8')"
    recipe = ["FROM " + BASE_TAG, "USER 0:0",
              f"COPY --chown={graph_file['uid']}:{graph_file['gid']} --chmod={graph_file['mode']:04o} graph.py /app/{GRAPH}",
              "RUN " + json.dumps(["python", "-c", regenerate]),
              'LABEL org.deepresearch.runtime.source-state="working-tree-prompt-only-not-publication"',
              'LABEL org.deepresearch.project.source_revision="working-tree-sha256:' + NEW_GRAPH + '"',
              "USER " + base["Config"]["User"], ""]
    (context / "Dockerfile").write_text("\n".join(recipe), encoding="utf8", newline="\n")
    command = ["docker", "buildx", "build", "--load", "--network=none", "--pull=false", "--progress=plain", "--tag", tag, str(context)]
    write(out / "build-command.json", command)
    with (out / "build.log").open("wb") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
    actual, = read_inspection(tag)
    assert {k: v for k, v in actual["Config"].items() if k != "Labels"} == prior["runtime_config"]
    after = inventory(actual["Id"], out / "runtime-inventory.json")
    runtime = gate.probe_image(actual["Id"], prior)
    write(out / "runtime-probe.json", runtime)
    assert after["native_files"] == before["native_files"]
    assert runtime["native_elf_closure"] == after["native_elf_closure"] == prior["native_elf_closure"]
    assert runtime["python_source_closure"] == after["python_source_closure"]
    old_python = {r["path"]: r["sha256"] for r in before["python_files"]}
    new_python = {r["path"]: r["sha256"] for r in after["python_files"]}
    python_delta = {p: {"before": old_python.get(p), "after": new_python.get(p)} for p in old_python.keys() | new_python.keys() if old_python.get(p) != new_python.get(p)}
    assert python_delta == {"/app/" + GRAPH: {"before": OLD_GRAPH, "after": NEW_GRAPH}}
    after_facts = capture(actual["Id"], out / "candidate-filesystem")
    new_files = {r["path"]: r for r in after_facts["runtime_manifest"]}
    fs_delta = {p: {"before": old_files.get(p), "after": new_files.get(p)} for p in old_files.keys() | new_files.keys() if old_files.get(p) != new_files.get(p)}
    assert set(fs_delta) == {"app/" + GRAPH, "app/source-fingerprint.json"}, sorted(fs_delta)
    write(out / "filesystem-delta.json", fs_delta)
    with tarfile.open(out / "candidate-filesystem/rootfs.tar") as archive:
        fingerprint = json.load(archive.extractfile("app/source-fingerprint.json"))
        assert archive.extractfile("app/" + GRAPH).read() == (ROOT / GRAPH).read_bytes()
    expected = dict(old_fingerprint, **{GRAPH: NEW_GRAPH})
    assert fingerprint == expected
    raw_config = gate.capture_actual_config(actual)
    (out / "image-config.raw.json").write_bytes(raw_config)
    write(out / "image-inspection.json", actual)
    scan = out / "scan"
    scan.mkdir()
    (scan / "empty-trivy.yaml").write_text("{}\n", encoding="utf8")
    (scan / "empty.ignore").write_bytes(b"")
    scanner = ROOT / ".cache/tools/trivy.exe"
    command = [str(scanner), "image", "--skip-db-update", "--skip-java-db-update", "--image-src", "docker", "--config", str(scan / "empty-trivy.yaml"), "--ignorefile", str(scan / "empty.ignore"), "--scanners", "vuln,secret", "--list-all-pkgs", "--severity", "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL", "--ignore-unfixed=false", "--exit-code", "0", "--timeout", "10m", "--format", "json", "--output", str(scan / "raw.json"), actual["Id"]]
    started = time.time()
    with (scan / "scanner.log").open("wb") as stream:
        completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    write(scan / "scan-receipt.json", {"command": command, "exit_code": completed.returncode, "started_epoch": started, "finished_epoch": time.time(), "scanner_sha256": sha(scanner), "raw_sha256": sha(scan / "raw.json"), "raw_report_modified": False, "filters_empty": True})
    completed.check_returncode()
    draft = deepcopy(prior)
    draft["python_source_closure"] = runtime["python_source_closure"]
    verdict = gate.adjudicate(read(scan / "raw.json"), actual, runtime, draft, actual_config_raw=raw_config, scan_source="docker")
    verdict.update(preparation_only=True, production_profile_not_yet_assembled=True, prior_profile_name_used_for_strict_fact_comparison_only=True)
    write(out / "preparation-verdict.json", verdict)
    receipt = {"schema": "agent-working-tree-two-prompt-candidate-v1", "base_engine_id": BASE, "base_config_digest": BASE_CONFIG,
               "base_profile": {"path": PRIOR_PROFILE, "sha256": PRIOR_SHA}, "engine_id": actual["Id"], "portable_config_digest": "sha256:" + hashlib.sha256(raw_config).hexdigest(),
               "source_state": "working-tree-not-final-source-commit", "parent_git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
               "graph_sha256": NEW_GRAPH, "test_sha256": sha(ROOT / TEST), "prompt_ast_delta": delta,
               "native_files": len(after["native_files"]), "native_elf_closure": after["native_elf_closure"], "python_source_closure": after["python_source_closure"],
               "python_file_delta": python_delta, "all_other_merged_filesystem_bytes_and_metadata_equal": True,
               "only_filesystem_delta": sorted(fs_delta), "all97_fingerprint_entries_equal_actual_files": True,
               "source_inputs": {p: sha(ROOT / p) for p in [GRAPH, TEST, "scripts/prepare_agent_gap_coverage_20261115.py"]},
               "provider_http": 0, "published": False, "runtime_configuration_except_labels_unchanged": True}
    write(out / "candidate-receipt.json", receipt)
    print(json.dumps(receipt))


def read_inspection(image):
    return json.loads(subprocess.check_output(["docker", "image", "inspect", image]))


def derive(directory, output):
    prior, old_review = checked_prior()
    receipt = read(directory / "candidate-receipt.json")
    assert receipt["graph_sha256"] == sha(ROOT / GRAPH) == NEW_GRAPH
    assert all(sha(ROOT / p) == h for p, h in receipt["source_inputs"].items())
    report = read(directory / "scan/raw.json")
    assert not any(r.get("Secrets") for r in report["Results"])
    findings = [gate.finding_tuple(r, f) for r in report["Results"] for f in r.get("Vulnerabilities", [])]
    packages = [gate.package_tuple(r, p) for r in report["Results"] for p in r["Packages"]]
    assert Counter(map(gate.canonical, findings)) == Counter(gate.canonical(x["tuple"]) for x in prior["findings"])
    assert Counter(map(gate.canonical, packages)) == Counter(map(gate.canonical, prior["packages"]))
    assert len(findings) == 92 and len(packages) == 125
    EVIDENCE.mkdir(exist_ok=True)
    sources = ["candidate-receipt.json", "filesystem-delta.json", "base-image-config.raw.json", "image-config.raw.json", "image-inspection.json", "base-runtime-inventory.json", "runtime-inventory.json", "runtime-probe.json", "preparation-verdict.json", "scan/raw.json", "scan/scan-receipt.json", "base-filesystem/runtime-manifest.json.gz", "candidate-filesystem/runtime-manifest.json.gz"]
    evidence = []
    for name in sources:
        source = directory / name
        target = EVIDENCE / name.replace("/", "-")
        raw = source.read_bytes()
        if name.endswith("inventory.json") or name == "scan/raw.json":
            target = target.with_suffix(target.suffix + ".gz")
            raw = gzip.compress(raw, mtime=0)
        if target.exists():
            assert target.read_bytes() == raw
        else:
            target.write_bytes(raw)
        evidence.append({"path": target.relative_to(ROOT).as_posix(), "sha256": sha(target)})
    additional = deepcopy(old_review["additional_evidence_files"])
    for item in additional:
        if item["path"] == GRAPH:
            assert item["sha256"] == OLD_GRAPH
            item["sha256"] = NEW_GRAPH
        else:
            assert sha(ROOT / item["path"]) == item["sha256"]
    for path in [PRIOR_PROFILE, prior["source_review_evidence"]["path"], TEST, "scripts/prepare_agent_gap_coverage_20261115.py"]:
        if path not in {x["path"] for x in additional}:
            additional.append({"path": path, "sha256": sha(ROOT / path)})
    additional.extend(evidence)
    review = {"schema": "agent-measured-prompt-only-continuation-v1", "actor": "/root/milvus_build", "actor_kind": "automated_technical_source_review", "parent_independent_review_required_before_publication": True,
              "risk_acceptance": False, "binary_redistribution_approved": False, "source_state": receipt["source_state"], "retained_prior_profile": receipt["base_profile"],
              "exact_ast_delta": receipt["prompt_ast_delta"], "application_behavior_acceptance_separate": True, "old_real_answered_false_result_unchanged": True,
              "native_and_all_other_python_files_exact_prior_bytes": True, "old92_tuples_and125_package_inventory_retained": True,
              "old_soupsieve_profile_and_original_reports_unchanged": True, "final_source_fingerprint_and_scratch_scan_still_required": True, "additional_evidence_files": additional}
    write(REVIEW, review)
    profile = deepcopy(prior)
    profile.update(name=NAME, python_source_closure=receipt["python_source_closure"], source_review_evidence={"path": REVIEW.relative_to(ROOT).as_posix(), "sha256": sha(REVIEW)},
                   review_scope="Exact measured two-prompt-only graph continuation; all515 native ELF and all other Python files match prior accepted Soup4.1 bytes; complete92 findings/125packages remain exact. Working-tree preparation, final source identity and behavior acceptance separate.")
    write(output, profile)
    print(json.dumps({"draft": str(output), "profile_sha256": sha(output), "review_sha256": sha(REVIEW), "independent_reviews_pending": True, "provider_http": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    prepare = sub.add_parser("candidate")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--tag", default="dr-v015-agent-gap-coverage:20261115")
    profile = sub.add_parser("derive")
    profile.add_argument("--candidate-dir", type=Path, required=True)
    profile.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "candidate":
        candidate(args.output.resolve(), args.tag)
    else:
        derive(args.candidate_dir.resolve(), args.output.resolve())
