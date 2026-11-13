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
from scripts import check_grafana_release_20261107 as candidate  # noqa: E402

APPROVED_MINIO = "deepresearch-approved-minio:20261026"
APPROVED_GRAFANA = candidate.APPROVED_TAG
SOURCE_GRAFANA = 'deepresearch-grafana-source:20261112'
SOURCE_PROFILE = 'grafana-source-20261112'
SOURCE_DOCKERFILE = 'deployment/docker/Dockerfile.grafana-20261112'
SOURCE_PROFILE_PATH = Path('deployment/security/grafana-source-20261112-profile.json')
SOURCE_MINIO = 'deepresearch-minio-source:20261113'
MINIO_PROFILE = 'minio-os-source-20261113'


def load_grafana_binding(root):
    """The new namespace has its own exact request and genuine authorization."""
    request = candidate.load_request(root)
    auth = candidate.read_json(candidate.repository_file(root, candidate.AUTH_PATH.as_posix()))
    candidate.validate_authorization(auth, request)
    if request['binding']['approved_tag'] != APPROVED_GRAFANA:
        raise ValueError('Current Grafana image namespace differs from its new approval')
    return request['binding']


def load_grafana_source_profile(root):
    from scripts import check_grafana_source_build_20261112 as source_candidate

    profile = source_candidate.load_profile(root)
    if (profile['profile'] != SOURCE_PROFILE or profile['image'] != SOURCE_GRAFANA
            or profile['context'] != '.' or profile['dockerfile'] != SOURCE_DOCKERFILE
            or profile['source_inputs'].get(SOURCE_DOCKERFILE) != candidate.file_hash(root / SOURCE_DOCKERFILE)):
        raise ValueError('Grafana source profile does not bind the exact current build entry')
    return profile


def grafana_selection(root):
    load_grafana_source_profile(root)
    digest = candidate.file_hash(root / SOURCE_PROFILE_PATH)
    return {'image': SOURCE_GRAFANA, 'profile': 'grafana-source-20261112',
            'profile_sha256': digest, 'mode': 'source_build', 'published': False,
            'subject': {'build_profile': SOURCE_PROFILE, 'profile_sha256': digest}}


def load_minio_source_profile(root):
    from scripts import check_minio_os_20261113 as source
    profile = source.load_profile(root)
    if (profile['profile'] != MINIO_PROFILE or profile['image'] != SOURCE_MINIO
            or profile['context'] != '.' or profile['dockerfile'] != source.DOCKERFILE
            or profile['source_inputs'].get(source.DOCKERFILE) != candidate.file_hash(root / source.DOCKERFILE)):
        raise ValueError('MinIO source profile does not bind the current build entry')
    return profile


def matrices(configuration, root):
    """Never let a rebuild or another service inherit the fixed-artifact approval."""
    minio = configuration.get("services", {}).get("minio", {})
    if minio.get('build'):
        raise ValueError('MinIO source builds must use the profile-bound build entry')
    if minio.get('image') == SOURCE_MINIO:
        load_minio_source_profile(root)
    elif minio.get('image') == APPROVED_MINIO:
        load_binding(root)
    else:
        raise ValueError('MinIO must select its exact artifact or reviewed source profile')
    if "grafana" in configuration.get("services", {}):
        grafana = configuration["services"]["grafana"]
        if grafana.get("build"):
            raise ValueError('Grafana source builds must use the profile-bound build entry')
        if grafana.get('image') == SOURCE_GRAFANA:
            load_grafana_source_profile(root)
        elif grafana.get('image') == APPROVED_GRAFANA:
            load_grafana_binding(root)
        else:
            raise ValueError('Grafana must select an exact approved artifact or the reviewed source profile')
    by_reference, custom = {}, {}
    for name, definition in configuration["services"].items():
        reference = definition.get("image")
        if reference and "@" in reference and not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", reference):
            raise ValueError("Runtime image digest must be a complete SHA256 descriptor")
        if ((reference in {APPROVED_MINIO, SOURCE_MINIO} and name != "minio")
                or (reference in {APPROVED_GRAFANA, SOURCE_GRAFANA} and name != "grafana")):
            raise ValueError("Approved or source-profile namespaces cannot spread to another service")
        if definition.get("build"):
            build = definition["build"]
            context = Path(build["context"]).resolve()
            if not context.is_relative_to(root) or build.get("args") or build.get("target"):
                raise ValueError("Custom build context/args require explicit security inventory support")
            filename = build.get("dockerfile", "Dockerfile")
            key = (str(context.relative_to(root)).replace("\\", "/") or ".", filename)
            if name == 'backend' and key != ('.', 'business-backend/Dockerfile'):
                raise ValueError('Backend must include the exact reviewed BusyBox source build context')
            custom.setdefault(key, []).append(name)
            continue
        reference = definition.get("image")
        if not reference:
            raise ValueError("Runtime service has no inspectable image")
        by_reference.setdefault(reference, []).append(name)
    matrix = {"include": [{"name": "-".join(services), "image": image, "services": ",".join(services),
                            "approved_artifact": image in {APPROVED_MINIO, APPROVED_GRAFANA},
                            "artifact": "minio" if image in {APPROVED_MINIO, SOURCE_MINIO} else "grafana" if image in {APPROVED_GRAFANA, SOURCE_GRAFANA} else "",
                            "source_build_profile": SOURCE_PROFILE if image == SOURCE_GRAFANA else MINIO_PROFILE if image == SOURCE_MINIO else "",
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
