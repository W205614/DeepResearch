import copy
import gzip
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil

import pytest

from scripts import prepare_go_otel_profiles_20261114 as candidate
from scripts import grafana_inventory_20261115 as inventory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def materials():
    native = candidate.current.load_profile(ROOT)['native_evidence']
    prior = candidate.common.prior_profile(ROOT)
    profile = candidate.build_profile(prior, native)
    profile['source_inputs'] = candidate.source_inputs_for_20261114(prior)
    directory = ROOT / candidate.INVENTORY_EVIDENCE
    sbom = json.loads(candidate.inventory_bytes(directory, 'full-syft.json'))
    return native, prior, profile, directory, sbom



@pytest.fixture(autouse=True)
def recorded_scanner_time(materials, monkeypatch):
    # Replay the archived real scanner contract at its actual observation time;
    # current production invocations still require fresh reports and databases.
    directory = materials[3]
    trivy = json.loads(candidate.inventory_bytes(directory, 'raw-trivy.private.json'))
    grype = json.loads(candidate.inventory_bytes(directory, 'full-grype.json'))
    observed = max(datetime.fromisoformat(value.replace('Z', '+00:00'))
                   for value in [trivy['CreatedAt'], grype['descriptor']['timestamp']]) + timedelta(seconds=1)

    class RecordedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed.astimezone(tz) if tz else observed.replace(tzinfo=None)

    monkeypatch.setattr(candidate.current, 'datetime', RecordedTime)


def test_real_complete_inventory_and_unchanged_consumer(materials):
    native, prior, profile, directory, _ = materials
    facts = candidate.derive_inventory(copy.deepcopy(profile), prior, native, directory)
    assert (facts['prior_go_modules'], facts['current_go_modules']) == (1720, 1730)
    assert len(facts['newly_linked_modules']) == 10
    assert len(facts['trivy_changed_inventory_targets']) == 12
    assert facts['all_severity_findings'] == {'Trivy': 40, 'Grype': 10}


@pytest.mark.parametrize('mutation', ['module', 'checksum', 'duplicate', 'compiler', 'architecture', 'location', 'version'])
def test_same_count_native_sbom_corruption_blocks(materials, mutation):
    native, _, _, _, sbom = materials
    sbom = copy.deepcopy(sbom)
    artifact = next(a for a in sbom['artifacts'] if a['type'] == 'go-module' and a['metadata'].get('h1Digest'))
    if mutation == 'module':
        artifact['name'] += '/unexpected'
    elif mutation == 'checksum':
        artifact['metadata']['h1Digest'] = 'h1:unexpected'
    elif mutation == 'duplicate':
        second = next(a for a in sbom['artifacts'] if a['type'] == 'go-module' and a is not artifact)
        sbom['artifacts'][sbom['artifacts'].index(second)] = copy.deepcopy(artifact)
    elif mutation == 'compiler':
        artifact['metadata']['goCompiledVersion'] = 'go1.27.3'
    elif mutation == 'architecture':
        artifact['metadata']['architecture'] = 'arm64'
    elif mutation == 'location':
        artifact['locations'][0]['path'] = '/usr/bin/unreviewed'
    else:
        artifact['version'] = 'UNKNOWN'
    with pytest.raises(ValueError):
        inventory.native_module_inventory(sbom, native, 'native')


@pytest.mark.parametrize('mutation', ['module', 'duplicate', 'target', 'non_go'])
def test_trivy_inventory_cannot_set_its_own_expected(materials, tmp_path, mutation):
    native, prior, profile, original, _ = materials
    for source in original.iterdir():
        shutil.copyfile(source, tmp_path / source.name)
    path = tmp_path / candidate.INVENTORY_REPORTS['raw-trivy.private.json']
    raw = json.loads(gzip.decompress(path.read_bytes()))
    target = next(r for r in raw['Results'] if r.get('Type') == 'gobinary')
    if mutation == 'module':
        target['Packages'][0]['Name'] += '/unexpected'
    elif mutation == 'duplicate':
        target['Packages'][1] = copy.deepcopy(target['Packages'][0])
    elif mutation == 'target':
        target['Target'] = 'usr/bin/unreviewed'
    else:
        target = next(r for r in raw['Results'] if r.get('Class') == 'os-pkgs')
        target['Packages'][0]['Version'] += '-unexpected'
    path.write_bytes(gzip.compress(json.dumps(raw).encode(), mtime=0))
    with pytest.raises(ValueError):
        candidate.derive_inventory(copy.deepcopy(profile), prior, native, tmp_path)


def test_previous_raw_inventory_archive_is_not_overwritten(materials, tmp_path, monkeypatch):
    directory = materials[3]
    monkeypatch.setattr(candidate, 'ROOT', tmp_path)
    candidate.archive_inventory_inputs(directory)
    target = tmp_path / candidate.INVENTORY_EVIDENCE / candidate.INVENTORY_REPORTS['full-syft.json']
    changed = gzip.compress(b'previous different raw evidence', mtime=0)
    target.write_bytes(changed)
    with pytest.raises(ValueError, match='Previously archived raw inventory evidence'):
        candidate.archive_inventory_inputs(directory)
    assert target.read_bytes() == changed


def test_complete_profile_validator_recomputes_the_archived_inventory():
    profile = candidate.current.read(ROOT / candidate.current.PROFILE_PATH)
    assert candidate.current.validate_profile(profile, ROOT) is profile


@pytest.mark.parametrize('mutation', ['go-count', 'other-type', 'go-inventory', 'same-count-fact',
                                     'unbound-helper', 'unbound-report'])
def test_profile_validator_rejects_invented_inventory_or_unbound_proof(mutation):
    profile = copy.deepcopy(candidate.current.read(ROOT / candidate.current.PROFILE_PATH))
    if mutation == 'go-count':
        profile['expected']['syft_types']['go-module'] += 1
    elif mutation == 'other-type':
        profile['expected']['syft_types']['apk'] += 1
    elif mutation == 'go-inventory':
        profile['expected']['trivy_inventory_sha256']['usr/share/grafana/bin/grafana'] = '0' * 64
    elif mutation == 'same-count-fact':
        profile['inventory_migration_facts']['newly_linked_modules'][0]['module'] += '/unreviewed'
    elif mutation == 'unbound-helper':
        profile['evidence_sha256'].pop('scripts/grafana_inventory_20261115.py')
    else:
        profile['evidence_sha256'].pop(candidate.INVENTORY_EVIDENCE + 'full-syft.json.gz')
    with pytest.raises(ValueError):
        candidate.current.validate_profile(profile, ROOT)
