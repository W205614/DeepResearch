"""Fail on HIGH/CRITICAL CodeQL findings, including extension query packs."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'deployment/security/codeql-reviewed-findings-20261112-grafana-stable.json'


def result_rule(tool, result):
    reference = result.get('rule', {})
    component_reference = reference.get('toolComponent')
    component = tool['driver']
    if component_reference is not None:
        # SARIF 2.1.0: toolComponent.index addresses tool.extensions, not driver.
        extensions = tool.get('extensions', [])
        index = component_reference.get('index')
        if type(index) is not int or not 0 <= index < len(extensions):
            raise ValueError('Missing or invalid CodeQL extension component index')
        component = extensions[index]
        for key in ('name', 'guid'):
            if key in component_reference and component_reference[key] != component.get(key):
                raise ValueError('CodeQL component reference disagrees with its index')
    rules = component.get('rules', [])
    index = reference.get('index', result.get('ruleIndex'))
    rule_id = reference.get('id', result.get('ruleId'))
    if index is not None:
        if type(index) is not int or not 0 <= index < len(rules):
            raise ValueError('Invalid CodeQL rule index')
        rule = rules[index]
        if rule_id is not None and rule['id'] != rule_id:
            raise ValueError('CodeQL rule id disagrees with its index')
    else:
        matches = [rule for rule in rules if rule['id'] == rule_id]
        if len(matches) != 1:
            raise ValueError('CodeQL result has missing or ambiguous rule metadata')
        rule = matches[0]
    if 'ruleId' in result and result['ruleId'] != rule['id']:
        raise ValueError('CodeQL result ruleId disagrees with its rule reference')
    return rule


def blocking_findings(directory):
    files = sorted(directory.glob('*.sarif'))
    if not files:
        raise ValueError('CodeQL SARIF missing; cannot claim source scan passed')
    findings = []
    for path in files:
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        if document['version'] != '2.1.0' or not document['runs']:
            raise ValueError('Missing or unsupported CodeQL SARIF run')
        for run in document['runs']:
            if any(invocation.get('executionSuccessful') is False for invocation in run.get('invocations', [])):
                raise ValueError('CodeQL invocation did not complete successfully')
            for result in run['results']:
                rule = result_rule(run['tool'], result)
                properties = rule['properties']
                tags = properties['tags']
                if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                    raise ValueError('Invalid CodeQL rule tags')
                severity = properties.get('security-severity')
                if severity is None:
                    if 'security' in tags:
                        raise ValueError('CodeQL security rule is missing security-severity')
                    continue
                severity = float(severity)
                if not math.isfinite(severity) or not 0 <= severity <= 10:
                    raise ValueError('Invalid CodeQL security-severity')
                if severity >= 7:
                    findings.append({'sarif': path.name, 'rule_id': rule['id'], 'result': result})
    return findings


def finding_identity(finding):
    result = finding['result']
    locations = result['locations']
    if len(locations) != 1:
        raise ValueError('An exact reviewed CodeQL result must have one primary location')
    physical = locations[0]['physicalLocation']
    region = physical['region']
    return {'rule_id': finding['rule_id'], 'path': unquote(physical['artifactLocation']['uri']),
            'start_line': region['startLine'], 'end_line': region.get('endLine', region['startLine']),
            'start_column': region['startColumn'], 'end_column': region['endColumn'],
            'message': result['message']['text']}


def reviewed_findings(findings, review_path, root=ROOT):
    """Account for proved false positives without filtering the uploaded SARIF."""
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review['schema'] != 'codeql-exact-reviewed-findings-v1':
        raise ValueError('Unsupported CodeQL review record')
    records = review['findings']
    identities = []
    for record in records:
        if record['reason'] != 'false positive' or not record['justification'].strip():
            raise ValueError('CodeQL technical review cannot accept unresolved risk')
        identity = record['identity']
        if identity in identities:
            raise ValueError('Duplicate CodeQL reviewed result')
        identities.append(identity)
        bindings = record['evidence_sha256']
        if identity['path'] not in bindings or len(bindings) < 2:
            raise ValueError('CodeQL review must bind both the source and its proof')
        for name, expected in bindings.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError('Missing or escaping CodeQL review evidence')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('CodeQL reviewed source or proof changed; renewed review required: ' + name)
    reviewed, unresolved = [], []
    for finding in findings:
        identity = finding_identity(finding)
        if identity in identities:
            reviewed.append(finding)
        else:
            unresolved.append(finding)
    return reviewed, unresolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--reviewed-findings', type=Path, default=REVIEW)
    args = parser.parse_args()
    findings = blocking_findings(args.directory)
    reviewed, unresolved = reviewed_findings(findings, args.reviewed_findings)
    for finding in unresolved:
        print(f"{finding['sarif']}: {finding['rule_id']}")
    print(f'CodeQL HIGH/CRITICAL security findings: {len(findings)}')
    print(f'Exactly reviewed false positives: {len(reviewed)}; unresolved: {len(unresolved)}')
    return int(bool(unresolved))


if __name__ == '__main__':
    raise SystemExit(main())
