"""Verify only the reviewed c6 build inputs; no runtime approval or normalization."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SOURCE_COMMIT = 'c6acf98ca5d753b6981153f49108f43c62dbef0d'
UPSTREAM_COMMIT = 'ce471d869a4810cd83c987dd3521c4b67689dbb6'
AGGREGATE_SHA256 = 'ef82bd49e4b501948107a6e18ed4f110fad3512b598de41cfc21d4161c73737e'
TEXT_INPUT = 'scripts/oci_artifact.py'
TEXT_RAW_SHA256 = 'e998b32de3a589ff091dc490a81ecfd34db1ed47b6701c950222909b6336e68b'
TEXT_COMMITTED_SHA256 = 'd620ca47abc9a96b1f2b0408847ad2918858515fd547beab18d417c6fd4ce537'
TEXT_BLOB = '81b01cdfb9045f461eb53ab4cb60ca16e66fcc72'


def verify_build_identity(root: Path, identity: dict):
    if identity['source_commit'] != SOURCE_COMMIT or identity['upstream_commit'] != UPSTREAM_COMMIT:
        raise ValueError('Another build source cannot inherit this release profile')
    raw, committed = identity['actual_build_input_sha256'], identity['committed_input_sha256']
    provenance = identity['provenance']
    aggregate = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (aggregate != AGGREGATE_SHA256 or len(raw) != 51 or set(raw) != set(committed)
            or provenance['project_source_commit'] != SOURCE_COMMIT
            or provenance['upstream_grafana_commit'] != UPSTREAM_COMMIT
            or provenance['input_files_sha256'] != raw
            or provenance['aggregate_inputs_sha256'] != AGGREGATE_SHA256
            or provenance['committed_source_identity_verified'] is not True
            or provenance['git_normalized_text_inputs_equal_committed_blobs'] is not True):
        raise ValueError('Complete reviewed build provenance differs')
    difference = {name for name in raw if raw[name] != committed[name]}
    normalization = provenance['git_text_normalizations']
    if difference != {TEXT_INPUT} or set(normalization) != {TEXT_INPUT}:
        raise ValueError('Only the single reviewed text input has two exact physical identities')
    proof = normalization[TEXT_INPUT]
    if (raw[TEXT_INPUT] != TEXT_RAW_SHA256 or committed[TEXT_INPUT] != TEXT_COMMITTED_SHA256
            or proof['working_sha256'] != TEXT_RAW_SHA256
            or proof['committed_sha256'] != TEXT_COMMITTED_SHA256
            or proof['git_blob_object_id'] != TEXT_BLOB
            or proof['git_attributes'] != {'text': 'unspecified', 'eol': 'unspecified',
                'filter': 'unspecified', 'working-tree-encoding': 'unspecified'}
            or proof['git_eol_classification'] != 'i/lf    w/crlf  attr/                 \tscripts/oci_artifact.py'
            or proof['normalization'] != 'Git-classified text CRLF-to-LF only'):
        raise ValueError('The actual Git clean/provenance proof differs')
    observed = {}
    resolved = root.resolve()
    for name in sorted(raw):
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(resolved):
            raise ValueError('A reviewed build input is absent, linked or outside the checkout')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        # Accept two explicitly reviewed hashes for one file; never rewrite bytes
        # or accept arbitrary CRLF differences in evidence or other source files.
        allowed = {TEXT_RAW_SHA256, TEXT_COMMITTED_SHA256} if name == TEXT_INPUT else {raw[name]}
        if digest not in allowed:
            raise ValueError('Reviewed build input bytes changed: ' + name
                + '; use a separate reviewed checkout created with git clone --config core.autocrlf=false. '
                  'Preserve the existing workspace; no automatic normalization is allowed.')
        observed[name] = digest
    return {'build_source_commit': SOURCE_COMMIT, 'actual_input_count': 51,
            'observed_input_sha256': observed, 'exact_dual_identity_paths': [TEXT_INPUT],
            'artifact_approved': False}
