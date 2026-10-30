"""Static runtime packaging guards; do not build images or claim TLS execution."""
import hashlib
import json
from pathlib import Path
import re
import shlex


ROOT = Path(__file__).resolve().parents[1]


def instructions():
    source = (ROOT / 'Dockerfile.milvus').read_text(encoding='utf-8')
    source = source.replace('\\\n', ' ')
    return [line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith('#')]


def test_final_image_copies_filtered_runtime_without_inheriting_sample_key_layers():
    lines = instructions()
    assert [line for line in lines if line.startswith('FROM ')][-1] == 'FROM scratch'
    package = lines[lines.index('FROM runtime-security AS runtime-package'):lines.index('FROM scratch')]
    assert 'COPY --from=runtime-security / /runtime-root/' in package
    prune = next(line for line in package if line.startswith('RUN '))
    assert 'test -d /runtime-root/milvus/configs/cert' in prune
    assert 'test ! -L /runtime-root/milvus/configs/cert' in prune
    assert 'rm -r -- /runtime-root/milvus/configs/cert' in prune
    assert 'test ! -e /runtime-root/milvus/configs/cert' in prune
    assert 'test -f /runtime-root/milvus/configs/milvus.yaml' in prune
    final = lines[lines.index('FROM scratch') + 1:]
    copies = [line for line in final if line.startswith('COPY ')]
    assert copies == [
        'COPY --from=runtime-package /runtime-root/ /',
        'COPY --chmod=0755 --from=build /out/libmilvus-planparser.so /milvus/lib/libmilvus-planparser.so',
        'COPY --from=build /out/milvus /milvus/bin/milvus',
    ]
    assert not any(line.startswith(('ADD ', 'RUN ')) for line in final)


def test_repackaged_runtime_retains_exact_fixed_image_startup_contract():
    final = instructions()[instructions().index('FROM scratch') + 1:]
    env = next(line for line in final if line.startswith('ENV '))
    assert dict(item.split('=', 1) for item in shlex.split(env[4:])) == {
        'PATH': '/milvus/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'LD_LIBRARY_PATH': '/milvus/lib::/usr/lib', 'LD_PRELOAD': ':/milvus/lib/libjemalloc.so',
        'MALLOC_CONF': 'background_thread:true', 'OPENSSL_MODULES': '/milvus/lib/ossl-modules',
        'ASAN_OPTIONS': 'detect_leaks=1:detect_container_overflow=0',
    }
    assert 'USER milvus:milvus' in final
    assert 'WORKDIR /milvus/' in final
    entrypoint = next(line for line in final if line.startswith('ENTRYPOINT '))
    assert json.loads(entrypoint.removeprefix('ENTRYPOINT ')) == ['/tini', '--']
    assert next(line for line in final if line.startswith('LABEL ')) == (
        'LABEL org.opencontainers.image.ref.name=ubuntu org.opencontainers.image.version=22.04')
    # These fields were absent from the pinned original Config. Do not invent defaults.
    assert not any(re.match(r'^(CMD|VOLUME|EXPOSE|HEALTHCHECK|STOPSIGNAL|SHELL)\s', line) for line in final)


def test_internal_tls_patch_is_hash_bound_and_tests_are_required_before_runtime_build():
    vendor = ROOT / 'deployment/vendors/milvus'
    locked = json.loads((vendor / 'source.lock.json').read_text(encoding='utf-8'))
    patch = locked['maintainer_internal_TLS_source_patch_20261027']
    lines = instructions()
    for name, hash_key in ((patch['patch_file'], 'patch_file_sha256'), (patch['test_file'], 'test_file_sha256')):
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == patch[hash_key]
        assert locked['files'][name] == patch[hash_key]
    application = next(line for line in lines if line.startswith('RUN echo ') and patch['original_file_sha256'] in line)
    assert patch['patch_file_sha256'] in application
    assert patch['test_file_sha256'] in application
    assert patch['patched_file_sha256'] in application
    assert 'patch --batch --forward --fuzz=0 -p1' in application
    assert application.index(patch['original_file_sha256']) < application.index('patch --batch')
    assert application.index('patch --batch') < application.index(patch['patched_file_sha256'])
    test = next(line for line in lines if patch['Go_test_command'] in line)
    main = next(line for line in lines if '-o /out/milvus ./cmd/main.go' in line)
    assert lines.index(test) < lines.index(main) < lines.index('FROM scratch')
