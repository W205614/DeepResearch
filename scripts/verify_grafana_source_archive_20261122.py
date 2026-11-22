"""Verify the complete official Grafana source tree independent of gzip encoding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile


TREE_SHA256 = '2d9a503adcce2c8d5037dbfcfef1a1b75402e6991d9947b6a65587f217ded570'


def tree_fingerprint(path):
    members = {}
    root = None
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or not relative.parts or '..' in relative.parts:
                raise ValueError('Invalid Grafana source archive member')
            if root is None:
                if len(relative.parts) != 1 or not member.isdir():
                    raise ValueError('Grafana source archive must have one top-level directory')
                root = relative.parts[0]
                continue
            if relative.parts[0] != root or len(relative.parts) < 2:
                raise ValueError('Grafana source member is outside the release tree')
            name = PurePosixPath(*relative.parts[1:]).as_posix()
            if name in members:
                raise ValueError('Duplicate Grafana source archive member: ' + name)
            if member.isdir():
                identity = ['directory', member.mode]
            elif member.isfile():
                with archive.extractfile(member) as content:
                    identity = ['file', member.mode, hashlib.file_digest(content, 'sha256').hexdigest()]
            elif member.issym():
                identity = ['symlink', member.mode, member.linkname]
            else:
                raise ValueError('Unsupported Grafana source archive member: ' + name)
            members[name] = identity
    raw = json.dumps(members, sort_keys=True, separators=(',', ':')).encode()
    return len(members), hashlib.sha256(raw).hexdigest()


def verify(path):
    count, actual = tree_fingerprint(path)
    if actual != TREE_SHA256:
        raise ValueError(f'Grafana release tree differs ({count} members, SHA-256 {actual})')
    with path.open('rb') as stream:
        archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'archive_bytes': path.stat().st_size, 'archive_sha256': archive_hash,
            'source_members': count, 'source_tree_sha256': actual}


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]))))
