"""Derive vendor-image CI matrix from the exact Compose source being checked."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.image_disposition import load_binding  # noqa: E402
from scripts import check_grafana_candidate_exact28 as candidate  # noqa: E402

APPROVED_MINIO = "deepresearch-approved-minio:20261026"
APPROVED_GRAFANA = "deepresearch-approved-grafana:20261028"


def load_grafana_binding(root):
    """The new namespace has its own exact request and genuine authorization."""
    request = candidate.load_request(root)
    auth = candidate.read_json(candidate.repository_file(root, candidate.AUTH_PATH.as_posix()))
    candidate.validate_authorization(auth, request)
    if request['binding']['approved_tag'] != APPROVED_GRAFANA:
        raise ValueError('Current Grafana image namespace differs from its new approval')
    return request['binding']


def grafana_selection(root):
    binding = load_grafana_binding(root)
    return {'image': APPROVED_GRAFANA, 'request_sha256': candidate.REQUEST_SHA256,
            'authorization_sha256': candidate.file_hash(root / candidate.AUTH_PATH),
            'authorization_verified': True, 'subject': candidate.subject_for(binding)}


def matrices(configuration, root):
    """Never let a rebuild or another service inherit the fixed-artifact approval."""
    minio = configuration.get("services", {}).get("minio", {})
    if minio.get("build") or minio.get("image") != APPROVED_MINIO:
        raise ValueError("MinIO must load the exact approved artifact; rebuild requires renewed review")
    load_binding(root)
    if "grafana" in configuration.get("services", {}):
        grafana = configuration["services"]["grafana"]
        if grafana.get("build") or grafana.get("image") != APPROVED_GRAFANA:
            raise ValueError("Grafana must load its independently approved artifact; rebuild requires renewed review")
        load_grafana_binding(root)
    by_reference, custom = {}, {}
    for name, definition in configuration["services"].items():
        reference = definition.get("image")
        if reference and "@" in reference and not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", reference):
            raise ValueError("Runtime image digest must be a complete SHA256 descriptor")
        if definition.get("build"):
            build = definition["build"]
            context = Path(build["context"]).resolve()
            if not context.is_relative_to(root) or build.get("args") or build.get("target"):
                raise ValueError("Custom build context/args require explicit security inventory support")
            filename = build.get("dockerfile", "Dockerfile")
            key = (str(context.relative_to(root)).replace("\\", "/") or ".", filename)
            custom.setdefault(key, []).append(name)
            continue
        reference = definition.get("image")
        if not reference:
            raise ValueError("Runtime service has no inspectable image")
        if (reference == APPROVED_MINIO and name != "minio") or (reference == APPROVED_GRAFANA and name != "grafana"):
            raise ValueError("Approved artifact namespaces cannot spread to another service")
        by_reference.setdefault(reference, []).append(name)
    matrix = {"include": [{"name": "-".join(services), "image": image, "services": ",".join(services),
                            "approved_artifact": image in {APPROVED_MINIO, APPROVED_GRAFANA},
                            "artifact": "minio" if image == APPROVED_MINIO else "grafana" if image == APPROVED_GRAFANA else "",
                            "native_jvm_profile": "keycloak" if "keycloak" in services else ""}
                           for image, services in sorted(by_reference.items())]}
    custom_matrix = {"include": [{"name": services[0], "context": key[0], "dockerfile": key[1], "services": ",".join(services),
                                 "native_jvm_profile": "keycloak" if "keycloak" in services else "business-java" if "backend" in services else ""}
                                for key, services in sorted(custom.items())]}
    return matrix, custom_matrix


def resolve_configuration(root):
    """Read Compose only; never create, start, stop, or remove containers."""
    compose = root / 'compose.yaml'
    environment = dict(os.environ)
    for key in re.findall(r'\$\{([A-Z][A-Z0-9_]*):\?', compose.read_text(encoding='utf-8')):
        environment[key] = 'ci-inventory-fixture-' + key.lower()
    empty = root / '.cache/runtime-inventory-empty.env'
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text('', encoding='utf-8')
    result = subprocess.run(['docker', 'compose', '--env-file', str(empty), '-f', str(compose), '-p', 'dr-ci-inventory',
                             'config', '--format', 'json'], env=environment, cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('Compose inventory cannot be resolved; private output withheld')
    return candidate.strict_json(result.stdout)


def build_record(root=ROOT):
    configuration = resolve_configuration(root)
    matrix, custom_matrix = matrices(configuration, root)
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    workflow_files = sorted(p.relative_to(root).as_posix() for p in (root / '.github/workflows').iterdir()
                            if p.is_file() and p.suffix in {'.yml', '.yaml'})
    return {'schema': 'runtime-image-inventory-v2', 'source_commit': head,
            'github_sha': os.environ.get('GITHUB_SHA'),
            'compose_sha256': candidate.file_hash(root / 'compose.yaml'),
            'workflow_sha256': {name: candidate.file_hash(root / name) for name in workflow_files},
            'resolved_configuration_sha256': hashlib.sha256(json.dumps(configuration, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest(),
            'vendor_matrix': matrix, 'custom_matrix': custom_matrix,
            'grafana_selection': grafana_selection(root)}


def inventory():
    root = Path(__file__).resolve().parents[1]
    record = build_record(root)
    matrix, custom_matrix = record['vendor_matrix'], record['custom_matrix']
    output = root / '.cache/eval/runtime-image-inventory.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2), encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write('matrix=' + json.dumps(matrix, separators=(',', ':')) + '\n')
            stream.write('custom_matrix=' + json.dumps(custom_matrix, separators=(',', ':')) + '\n')
    return record


if __name__ == '__main__':
    value = inventory()
    print(json.dumps({'vendor_images': len(value['vendor_matrix']['include']), 'compose_sha256': value['compose_sha256']}))
