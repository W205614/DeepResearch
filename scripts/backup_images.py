"""Streaming content proof for multi-image, current-platform recovery bundles.

Independent of single-artifact approval/VEX limits. No extraction or Docker cache.
"""
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

MAX_BUNDLE_BYTES = 32 * 1024**3
MAX_UNCOMPRESSED_BYTES = 64 * 1024**3
MAX_METADATA_BYTES = 4 * 1024**2
INDEX = {'application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST = {'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}
CONFIG = {'application/vnd.oci.image.config.v1+json', 'application/vnd.docker.container.image.v1+json'}
RAW = {'application/vnd.oci.image.layer.v1.tar', 'application/vnd.docker.image.rootfs.diff.tar'}
GZIP = {'application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.docker.image.rootfs.diff.tar.gzip'}


def verify_image_bundle(path, expected_ids, *, os_name='linux', architecture='amd64'):
    """Verify bound roots, selected chains and real DiffIDs, not other architectures."""
    path = Path(path)
    expected_ids = set(expected_ids)
    if (not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_BUNDLE_BYTES
            or not 0 < len(expected_ids) <= 256
            or any(not re.fullmatch(r'sha256:[a-f0-9]{64}', value) for value in expected_ids)):
        raise ValueError('Recovery image bundle size/identity bound failed')
    original = path.stat()
    verified, metadata, decoded_layers, representations = set(), {}, {}, {}
    metadata_seen, metadata_bytes = set(), 0
    excluded, selected, expanded, visits = [], [], 0, 0
    try:
        with tarfile.open(path, 'r:') as archive:
            members = {}
            for member in archive:
                name = member.name
                if (len(members) >= 100000 or len(name) > 4096 or '\\' in name or '\0' in name
                        or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                        or name in members or not (member.isdir() or member.isfile())):
                    raise ValueError('Recovery image bundle has unsafe/ambiguous outer members')
                members[name] = member

            def document(name):
                nonlocal metadata_bytes
                member = members.get(name)
                if not member or not member.isfile() or member.size > MAX_METADATA_BYTES:
                    raise ValueError('Recovery image metadata is missing or oversized')
                if name not in metadata_seen:
                    metadata_bytes += member.size
                    metadata_seen.add(name)
                if metadata_bytes > 64 * 1024**2:
                    raise ValueError('Recovery aggregate image metadata bound exceeded')
                return json.load(archive.extractfile(member))

            def blob(descriptor, *, decode=False):
                digest = descriptor.get('digest')
                if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
                    raise ValueError('Recovery image descriptor digest is invalid')
                name = 'blobs/sha256/' + digest[7:]
                member = members.get(name)
                if (not member or not member.isfile() or type(descriptor.get('size')) is not int
                        or member.size != descriptor['size']):
                    raise ValueError('Required current-platform image blob is missing or has wrong size')
                kind = descriptor.get('mediaType')
                encoding = ('gzip' if kind in GZIP else 'raw' if kind in RAW else 'config' if kind in CONFIG
                            else 'index' if kind in INDEX else 'manifest' if kind in MANIFEST else kind)
                representation = (encoding, descriptor['size'])
                if digest in representations and representations[digest] != representation:
                    raise ValueError('Recovery repeated descriptor type/size is inconsistent')
                representations[digest] = representation
                if digest not in verified:
                    with archive.extractfile(member) as stream:
                        actual = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
                    if actual != digest:
                        raise ValueError('Required current-platform image blob digest differs')
                    verified.add(digest)
                if decode:
                    if digest not in metadata:
                        metadata[digest] = document(name)
                    return metadata[digest]
                return member

            def select(descriptor, ancestors=()):
                nonlocal visits
                visits += 1
                if visits > 100000:
                    raise ValueError('Recovery image descriptor traversal bound exceeded')
                digest = descriptor.get('digest')
                if len(ancestors) > 32 or digest in ancestors:
                    raise ValueError('Recovery image graph is cyclic or too deep')
                platform = descriptor.get('platform')
                if platform and (platform.get('os') != os_name or platform.get('architecture') != architecture):
                    excluded.append({'digest': digest, 'missing': 'blobs/sha256/'+str(digest)[7:] not in members})
                    return []
                kind = descriptor.get('mediaType')
                if kind in INDEX:
                    result = []
                    for child in blob(descriptor, decode=True).get('manifests', []):
                        result.extend(select(child, (*ancestors, digest)))
                    return result
                if kind not in MANIFEST:
                    raise ValueError('Recovery image root/manifest type is unsupported')
                manifest = blob(descriptor, decode=True)
                config_descriptor = manifest['config']
                if config_descriptor.get('mediaType') not in CONFIG:
                    raise ValueError('Recovery image config type is unsupported')
                config = blob(config_descriptor, decode=True)
                if config.get('os') != os_name or config.get('architecture') != architecture:
                    excluded.append({'digest': digest, 'missing': False})
                    return []
                return [(manifest, config)]

            if document('oci-layout').get('imageLayoutVersion') != '1.0.0':
                raise ValueError('Recovery requires a supported OCI Docker-save bundle')
            roots = document('index.json').get('manifests', [])
            if len(roots) != len(expected_ids) or {root.get('digest') for root in roots} != expected_ids:
                raise ValueError('Recovery image bundle roots differ from bound image identities')
            for root in roots:
                chain = select(root)
                if len(chain) != 1:
                    raise ValueError('Recovery image needs exactly one explicit current-platform manifest')
                selected.extend(chain)
            legacy = document('manifest.json')
            if not isinstance(legacy, list) or len(legacy) != len(selected):
                raise ValueError('Recovery Docker compatibility inventory differs')
            legacy_by_config = {entry['Config']: entry['Layers'] for entry in legacy}
            if len(legacy_by_config) != len(legacy):
                raise ValueError('Recovery Docker compatibility configs are ambiguous')
            for manifest, config in selected:
                layers = manifest.get('layers', [])
                diff_ids = config.get('rootfs', {}).get('diff_ids', [])
                if config.get('rootfs', {}).get('type') != 'layers' or len(layers) != len(diff_ids):
                    raise ValueError('Recovery image layer/DiffID inventory differs')
                config_path = 'blobs/sha256/' + manifest['config']['digest'][7:]
                layer_paths = ['blobs/sha256/' + layer['digest'][7:] for layer in layers]
                if legacy_by_config.get(config_path) != layer_paths:
                    raise ValueError('Recovery Docker compatibility chain differs from selected OCI image')
                for descriptor, expected in zip(layers, diff_ids, strict=True):
                    kind, digest = descriptor.get('mediaType'), descriptor.get('digest')
                    if kind not in RAW | GZIP:
                        raise ValueError('Recovery runtime layer compression is unsupported')
                    member = blob(descriptor)
                    if digest not in decoded_layers:
                        with archive.extractfile(member) as raw:
                            stream = gzip.GzipFile(fileobj=raw) if kind in GZIP else raw
                            try:
                                hashed = hashlib.sha256()
                                while block := stream.read(1024 * 1024):
                                    expanded += len(block)
                                    if expanded > MAX_UNCOMPRESSED_BYTES:
                                        raise ValueError('Recovery aggregate expanded layer bound exceeded')
                                    hashed.update(block)
                                decoded_layers[digest] = 'sha256:' + hashed.hexdigest()
                            finally:
                                if stream is not raw:
                                    stream.close()
                    if decoded_layers[digest] != expected:
                        raise ValueError('Recovery actual uncompressed layer DiffID differs')
    except (tarfile.TarError, OSError, EOFError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Recovery image bundle is incomplete or unsupported') from error
    final = path.stat()
    if (original.st_size, original.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
        raise ValueError('Recovery image bundle changed during verification')
    return {'version': 1, 'platform': os_name+'/'+architecture, 'image_roots': len(expected_ids), 'descriptor_visits': visits,
            'selected_manifests': len(selected), 'verified_required_blobs': len(verified),
            'verified_unique_layer_diffids': len(decoded_layers), 'expanded_layer_bytes': expanded,
            'archive_bytes': final.st_size, 'bundle_limit_bytes': MAX_BUNDLE_BYTES,
            'expanded_limit_bytes': MAX_UNCOMPRESSED_BYTES, 'excluded_platform_refs': len(excluded),
            'excluded_missing_platform_refs': sum(row['missing'] for row in excluded),
            'full_multiarch_archive_verified': False, 'docker_cache_consulted': False}
