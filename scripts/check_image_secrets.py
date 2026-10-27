"""Secret findings have no approved-image vulnerability disposition.

The calling workflow must complete a Trivy vuln,secret scan first. Its separate
artifact disposition still validates the image identity and vulnerability rows.
Never include matched values or source excerpts in this helper's output.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def assess(scan, scanner_exit_code=0):
    if scanner_exit_code != 0:
        raise ValueError('Image scanner did not complete successfully')
    if (not isinstance(scan, dict) or scan.get('SchemaVersion') != 2
            or scan.get('ArtifactType') != 'container_image'
            or not isinstance(scan.get('Trivy'), dict) or scan['Trivy'].get('Version') != '0.75.0'
            or not isinstance(scan.get('Results'), list) or not scan['Results']):
        raise ValueError('Missing or unsupported image scan evidence')
    if any(field in scan and (not isinstance(scan[field], list) or scan[field])
           for field in ('Errors', 'ModifiedFindings', 'SuppressedFindings')):
        raise ValueError('Incomplete or filtered image scan evidence')
    levels = Counter()
    for result in scan['Results']:
        if not isinstance(result, dict) or not isinstance(result.get('Target'), str) or not result['Target']:
            raise ValueError('Malformed image scan target')
        if any(field in result and (not isinstance(result[field], list) or result[field])
               for field in ('Errors', 'ModifiedFindings', 'SuppressedFindings')):
            raise ValueError('Incomplete or filtered image scan target')
        findings = result.get('Secrets', [])
        if not isinstance(findings, list):
            raise ValueError('Malformed image secret inventory')
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError('Malformed image secret finding')
            severity = finding.get('Severity', 'UNKNOWN')
            levels[severity if isinstance(severity, str) and severity in {'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'} else 'UNKNOWN'] += 1
    count = sum(levels.values())
    return {'passed': count == 0, 'mode': 'secret_gate_only_vulnerability_and_identity_gates_still_required',
            'secret_findings': count, 'secret_severity_counts': dict(levels),
            'secret_dispositions_allowed': False, 'scanner_context': 'completed Trivy0.75.0 vuln,secret workflow step'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scan', type=Path, required=True)
    parser.add_argument('--scanner-exit-code', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if not 0 < args.scan.stat().st_size <= 64 * 1024**2:
            raise ValueError('Image scan size bound failed')
        raw = args.scan.read_bytes()
        report = assess(json.loads(raw), args.scanner_exit_code)
        report['raw_scan_sha256'] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError, AttributeError) as error:
        report = {'passed': False, 'mode': 'failed_closed', 'error_type': type(error).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))
    raise SystemExit(0 if report['passed'] else 1)
