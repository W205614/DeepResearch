from pathlib import Path

import yaml


def source_steps():
    source = Path(__file__).parents[1] / '.github/workflows/security.yml'
    return yaml.safe_load(source.read_text(encoding='utf-8'))['jobs']['dependency-and-image-scan']['steps']


def test_raw_source_scan_completion_cannot_replace_the_independent_technical_gate():
    steps = source_steps()
    producer = next(i for i, step in enumerate(steps) if 'scripts/run_fs_source_scan.py' in step.get('run', ''))
    gate = next(i for i, step in enumerate(steps) if 'scripts/check_fs_source_applicability.py' in step.get('run', ''))
    assert producer < gate
    assert not steps[gate].get('if') and not steps[gate].get('continue-on-error')
    assert all(not step.get('continue-on-error') for step in steps)
    assert '--scan .cache/eval/source-fs/raw.json' in steps[gate]['run']
    assert '--scan-receipt .cache/eval/source-fs/completion.json' in steps[gate]['run']
    setup = next(step for step in steps if step.get('uses', '').startswith('aquasecurity/setup-trivy@'))
    assert setup['uses'] == 'aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514'
    assert setup['with']['version'] == 'v0.75.0'


def test_raw_source_evidence_is_retained_when_the_independent_gate_rejects_it():
    steps = source_steps()
    converter = next(step for step in steps if 'trivy convert' in step.get('run', ''))
    assert 'always()' in converter['if']
    assert '.cache/eval/source-fs/raw.json' in converter['run']
    artifact = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert artifact['if'] == 'always()' and artifact['with']['if-no-files-found'] == 'error'
    for expected in ('source-fs/raw.json', 'source-fs/completion.json', 'source-fs-verdict.json', 'trivy-fs.sarif'):
        assert expected in artifact['with']['path']


def test_existing_high_critical_threshold_and_exact_technical_route_are_explicit():
    source = Path(__file__).parents[1] / '.github/workflows/security.yml'
    text = source.read_text(encoding='utf-8')
    assert 'original HIGH,CRITICAL source threshold' in text
    assert 'source16' not in text and 'runtime5' not in text
    assert text.count('severity: HIGH,CRITICAL') == 2
