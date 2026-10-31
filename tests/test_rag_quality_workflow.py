from pathlib import Path

import yaml


def test_rag_preparation_is_default_and_supplier_calls_are_opt_in():
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / '.github/workflows/rag-quality.yml').read_text(encoding='utf-8')
    )
    # PyYAML's YAML 1.1 loader reads the GitHub Actions "on" key as True.
    triggers = workflow.get('on', workflow.get(True))
    preparation = triggers['workflow_dispatch']['inputs']['prepare_only']
    assert preparation['type'] == 'boolean' and preparation['default'] is True
    job = workflow['jobs']['live-reranker-evaluation']
    assert 'env' not in job
    supplier_steps = [
        step for step in job['steps']
        if any('secrets.' in str(value) for value in step.get('env', {}).values())
    ]
    assert len(supplier_steps) == 3
    for step in supplier_steps:
        assert step['if'] == '${{ !inputs.prepare_only }}'
        assert '--user "$(id -u):$(id -g)"' in step['run']


def test_rag_loads_approved_storage_before_starting_retrieval_services():
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / '.github/workflows/rag-quality.yml').read_text(encoding='utf-8')
    )
    commands = [step.get('run', '') for step in workflow['jobs']['live-reranker-evaluation']['steps']]
    proof = next(i for i, command in enumerate(commands) if 'scripts/check_artifact_source_reuse.py' in command)
    load = next(i for i, command in enumerate(commands) if 'scripts/fetch_approved_image.py' in command)
    start = next(i for i, command in enumerate(commands) if ' up ' in command)
    assert proof < load < start
    assert '--build --wait' in commands[start]
    preflight = next(command for command in commands if 'scripts/evaluate_retrieval.py' in command and '--help' in command)
    assert '--network none' in preflight
    assert 'scripts/evaluate_local_qa.py' in preflight
    assert '.cache/eval/rag-entrypoints.json' in preflight
