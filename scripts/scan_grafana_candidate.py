"""Collect complete pinned native reports for the newly authorized exact28 OCI.

Operational collection never grants a vulnerability disposition. The fresh raw
Trivy/secret gate and new exact28 consumer remain separate required steps.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_candidate_exact28 as gate  # noqa: E402
from scripts.check_native_jvm import digest, executable, prepare_database, run  # noqa: E402
from scripts.grafana_image_disposition import validate_loaded, validate_sbom  # noqa: E402


def collect(args):
    result = {'operational_success': False, 'security_gate_claim': False, 'artifact_approved': False}
    output_created = False
    try:
        # No tool download, database operation or Docker operation precedes the
        # independently pinned new request and its explicit new authorization.
        request = gate.load_request(ROOT)
        gate.validate_authorization(gate.read_json(ROOT / gate.AUTH_PATH), request)
        binding = request['binding']
        cache = (ROOT / '.cache/eval').resolve()
        gate.require(cache.is_relative_to(ROOT.resolve())
                     and args.output_dir.resolve().is_relative_to(cache)
                     and not args.output_dir.exists() and not args.output_dir.is_symlink(),
                     'Use a new collector directory under the workspace .cache/eval')
        args.output_dir.mkdir(parents=True, exist_ok=False)
        output_created = True
        actual = gate.strict_json(run(['docker', 'image', 'inspect', args.image]))
        gate.require(type(actual) is list and len(actual) == 1 and type(actual[0]) is dict,
                     'Exactly one actual loaded candidate image is required')
        actual = actual[0]
        validate_loaded(actual, binding)
        (args.output_dir / 'image-inspect.json').write_text(json.dumps(actual, indent=2), encoding='utf-8')
        lock = gate.read_json(ROOT / 'deployment/security/native-jvm-tools.json')
        syft = executable('syft', lock, args.tool_dir, args.tool_dir)
        grype = executable('grype', lock, args.tool_dir, args.tool_dir)
        env = {key: value for key, value in os.environ.items() if not key.startswith(('GRYPE_', 'SYFT_'))}
        env.update(GRYPE_CHECK_FOR_APP_UPDATE='false', SYFT_CHECK_FOR_APP_UPDATE='false')
        result['database'] = {}
        prepared = prepare_database(grype, lock['database'], args.db_cache,
                                    args.output_dir, env, result['database'])
        syconfig, grconfig = args.output_dir / 'syft-config.json', args.output_dir / 'grype-config.json'
        syconfig.write_text(json.dumps({'exclude': [], 'check-for-app-update': False}), encoding='utf-8')
        grconfig.write_text(json.dumps({'ignore': [], 'only-fixed': False, 'only-notfixed': False,
                                        'check-for-app-update': False}), encoding='utf-8')
        sbom, report = args.output_dir / 'full-syft.json', args.output_dir / 'full-grype.json'
        run([syft, '--config', syconfig, 'scan', 'docker:' + actual['Id'],
             '-o', 'syft-json=' + str(sbom), '--quiet'], env=prepared.env)
        types = validate_sbom(gate.read_json(sbom), binding)
        run([grype, '--config', grconfig, 'sbom:' + str(sbom),
             '-o', 'json', '--file', report, '--quiet'], env=prepared.env)
        prepared.assert_unchanged('after-full-scan')
        gate.require(type(gate.read_json(report).get('matches')) is list,
                     'Native scanner produced no complete structured report')
        result.update(operational_success=True, actual_image_id=actual['Id'], syft_types=types,
            sbom_sha256=digest(sbom), raw_grype_sha256=digest(report),
            request_sha256=gate.REQUEST_SHA256, new_subject_authorization_validated=True,
            old_authorization_inherited=False,
            required_next_step='check_grafana_candidate_exact28.py with full raw Trivy, '
                               'native/full Grype, actual inspection and exact authorized OCI')
    except Exception as error:
        result.update(error_type=type(error).__name__)
        if isinstance(error, ValueError):
            result['error'] = str(error)[:240]
    if output_created:
        (args.output_dir / 'collector.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--tool-dir', type=Path, default=ROOT / '.cache/native-jvm/tools')
    parser.add_argument('--db-cache', type=Path, default=ROOT / '.cache/native-jvm/grype-db')
    result = collect(parser.parse_args(argv))
    print(json.dumps({key: result[key] for key in
        ('operational_success', 'security_gate_claim', 'actual_image_id', 'error_type', 'error') if key in result}))
    return 0 if result['operational_success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
