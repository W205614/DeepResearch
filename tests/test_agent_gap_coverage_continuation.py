import pytest
import hashlib
import json
from pathlib import Path

from scripts import check_agent_full_scope as scope
from scripts.prepare_agent_gap_coverage_20261115 import audit_prompt_delta


BEFORE = '''
class ResearchGraph:
    def analyst(self):
        return self.ask("old analysis", cap=2)
    def validator(self):
        return self.ask("old coverage", cap=1)
    def writer(self):
        return self.ask("unchanged", cap=1)
'''
AFTER = BEFORE.replace('"old analysis"', '"new analysis"').replace('"old coverage"', '"new coverage"')


def test_exact_two_prompt_string_values_are_accepted():
    assert audit_prompt_delta(BEFORE, AFTER) == {
        "changed_methods": ["analyst", "validator"],
        "one_string_constant_each": True,
        "all_other_complete_module_ast_identical": True,
    }


@pytest.mark.parametrize("changed", [
    AFTER.replace("cap=2", "cap=3"),
    AFTER.replace('"unchanged"', '"another writer prompt"'),
    AFTER.replace('"new analysis"', '"old analysis"'),
    AFTER.replace('return self.ask("new analysis", cap=2)', 'return self.ask("new analysis", cap=2) or "new extra string"'),
    AFTER + '\n    def extra(self):\n        return 1\n',
    AFTER + '\nADDED_MODULE_STATE = True\n',
])
def test_prompt_continuation_rejects_any_unreviewed_ast_delta(changed):
    with pytest.raises(ValueError):
        audit_prompt_delta(BEFORE, changed)


def test_current_prompt_profile_keeps_all_prior_native_and_scanner_facts(released_v015_root, monkeypatch):
    monkeypatch.setattr(scope, 'ROOT', released_v015_root)
    current = scope.load_profile(scope.GAP_PROFILE_NAME)
    prior = json.loads(scope.SOUP41_PROFILE_PATH.read_bytes())
    assert current['python_source_closure']['files'] == prior['python_source_closure']['files'] == 5973
    assert current['python_source_closure'] != prior['python_source_closure']
    for key in ('native_elf_closure', 'official_glibc_payload', 'sqlite', 'runtime_config', 'findings', 'packages'):
        assert current[key] == prior[key]


def test_released_prompt_profile_rejects_changed_graph(released_v015_root, monkeypatch):
    monkeypatch.setattr(scope, 'ROOT', released_v015_root)
    current = scope.load_profile(scope.GAP_PROFILE_NAME)
    review = json.loads((scope.ROOT / current['source_review_evidence']['path']).read_bytes())
    graph = scope.ROOT / 'backend/research/graph.py'
    assert {'path': 'backend/research/graph.py', 'sha256': hashlib.sha256(graph.read_bytes()).hexdigest()} in review['additional_evidence_files']
    read = Path.read_bytes

    def changed(path):
        raw = read(path)
        return raw + b'\n# unreviewed prompt bytes\n' if path == graph else raw

    monkeypatch.setattr(Path, 'read_bytes', changed)
    with pytest.raises(ValueError, match='supporting source evidence changed'):
        scope.load_profile(scope.GAP_PROFILE_NAME)


def test_old_prompt_profile_cannot_approve_current_graph():
    with pytest.raises(ValueError, match='supporting source evidence changed'):
        scope.load_profile(scope.SOUP41_PROFILE_NAME)
    with pytest.raises(ValueError, match='supporting source evidence changed'):
        scope.load_profile(scope.GAP_PROFILE_NAME)
