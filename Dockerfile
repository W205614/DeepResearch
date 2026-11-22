FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2 AS runtime-base
FROM scratch AS agent-native-sources
ADD --checksum=sha256:a99e5beb8f568765d191f3d0d074cf6b60d2c89dec84f2f4bb70573e52e0a835 https://deb.debian.org/debian/pool/main/g/glibc/glibc_2.43-7.dsc /glibc_2.43-7.dsc
ADD --checksum=sha256:b8f0d50a7d4255658efe1f608cacdc4be82bb3c009875626df5968cc64c58880 https://deb.debian.org/debian/pool/main/g/glibc/glibc_2.43.orig.tar.xz /glibc_2.43.orig.tar.xz
ADD --checksum=sha256:9f62b90fb4c7423ed5023698c9014ad0086d26f9b6f46b2a163afcea7e581a02 https://deb.debian.org/debian/pool/main/g/glibc/glibc_2.43-7.debian.tar.xz /glibc_2.43-7.debian.tar.xz
ADD --checksum=sha256:7db1e202cce66617e9134a662c877a3a773de5b612fd52179815877e0ecfb847 https://ftp.debian.org/debian/pool/main/g/gcc-14/gcc-14_14.2.0-19.dsc /gcc-14_14.2.0-19.dsc
ADD --checksum=sha256:e162a5ef3f0077ecae598c6556908d2ab80841532df3398072a96a6df6e6aa29 https://ftp.debian.org/debian/pool/main/g/gcc-14/gcc-14_14.2.0.orig.tar.gz /gcc-14_14.2.0.orig.tar.gz
ADD --checksum=sha256:31cbab4bafa48aa1c540497bd8098cbcf98e673778f927651ed944709f687781 https://ftp.debian.org/debian/pool/main/g/gcc-14/gcc-14_14.2.0-19.debian.tar.xz /gcc-14_14.2.0-19.debian.tar.xz
ADD --checksum=sha256:e0fb736a48c483b655997d3c4c18a8c9991da03923af1ea4abd4ee883d93c59b https://codeload.github.com/openssl/openssl/legacy.tar.gz/f3acb0a3328046741479482c51cec815839e1b05 /openssl.tar.gz

FROM debian:trixie-slim@sha256:38a76d01668772e381ad2826d876627c89e7133e2f8a0f5d567306798b0f2a16 AS native-toolchain
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential binutils bison gawk dpkg-dev texinfo python3 ca-certificates \
    && test "$(gcc -dumpfullversion)" = 14.2.0 && rm -rf /var/lib/apt/lists/*
ENV SOURCE_DATE_EPOCH=1794873600 TZ=UTC LC_ALL=C

FROM native-toolchain AS glibc-security
COPY --from=agent-native-sources /glibc_2.43-7.dsc /glibc_2.43.orig.tar.xz /glibc_2.43-7.debian.tar.xz /inputs/
COPY deployment/vendors/agent-native-20261122/glibc-local-production-only.patch deployment/vendors/agent-native-20261122/glibc-iso2022-production-only.patch /patches/
WORKDIR /build
RUN echo 'a99e5beb8f568765d191f3d0d074cf6b60d2c89dec84f2f4bb70573e52e0a835  /inputs/glibc_2.43-7.dsc' | sha256sum -c - \
    && dpkg-source -x /inputs/glibc_2.43-7.dsc /src \
    && patch -d /src -p1 --fuzz=0 < /patches/glibc-local-production-only.patch \
    && patch -d /src -p1 --fuzz=0 < /patches/glibc-iso2022-production-only.patch \
    && ln -s x86_64-linux-gnu/asm /usr/include/asm \
    && /src/configure --prefix=/usr --libdir=/usr/lib/x86_64-linux-gnu --with-headers=/usr/include \
        --enable-kernel=4.19 --enable-stack-protector=strong --enable-bind-now --disable-werror \
    && (make -j4 > /build/make.log 2>&1 || { tail -25 /build/make.log; exit 1; }) \
    && install -D /build/libc.so /out/usr/lib/x86_64-linux-gnu/libc.so.6 \
    && install -D /build/elf/ld.so /out/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 \
    && for module in UHC ISO-2022-CN-EXT IBM1390 IBM1399; do \
         install -D -m 0644 "/build/iconvdata/$module.so" "/out/usr/lib/x86_64-linux-gnu/gconv/$module.so" || exit; \
       done \
    && find /out/usr/lib -type f -name '*.so*' -exec strip --strip-unneeded '{}' + \
    && install -D -m 0644 /src/COPYING.LIB /out/usr/share/licenses/glibc-source/COPYING.LIB

FROM native-toolchain AS stdcxx-security
COPY --from=agent-native-sources /gcc-14_14.2.0-19.dsc /gcc-14_14.2.0.orig.tar.gz /gcc-14_14.2.0-19.debian.tar.xz /inputs/
COPY deployment/vendors/agent-native-20261122/gcc-filebuf-production-only.patch /inputs/gcc-filebuf-production-only.patch
WORKDIR /build
RUN echo '7db1e202cce66617e9134a662c877a3a773de5b612fd52179815877e0ecfb847  /inputs/gcc-14_14.2.0-19.dsc' | sha256sum -c - \
    && dpkg-source -x /inputs/gcc-14_14.2.0-19.dsc /debian-gcc \
    && mkdir /src /build/config-headers \
    && tar -xJf /debian-gcc/gcc-14.2.0-dfsg.tar.xz -C /src --strip-components=1 \
    && patch -d /src -p1 --fuzz=0 < /inputs/gcc-filebuf-production-only.patch \
    && ln -s /src/libgcc/gthr-posix.h /build/config-headers/gthr-default.h \
    && CPPFLAGS='-I/build/config-headers -I/src/libgcc' \
       /src/libstdc++-v3/configure --prefix=/usr --libdir=/usr/lib/x86_64-linux-gnu \
         --disable-multilib --enable-shared --disable-static --disable-libstdcxx-pch --enable-libstdcxx-threads \
    && grep -F '#define _GLIBCXX_HAS_GTHREADS 1' /build/config.h \
    && (make -j4 > /build/make.log 2>&1 || { tail -25 /build/make.log; exit 1; }) \
    && install -D src/.libs/libstdc++.so.6.0.33 /out/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33 \
    && strip --strip-unneeded /out/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33 \
    && install -D -m 0644 /src/COPYING3 /out/usr/share/licenses/gcc-source/COPYING3 \
    && install -D -m 0644 /src/COPYING.RUNTIME /out/usr/share/licenses/gcc-source/COPYING.RUNTIME

FROM debian:trixie-slim@sha256:38a76d01668772e381ad2826d876627c89e7133e2f8a0f5d567306798b0f2a16 AS openssl-security
# Keep the upstream 3.5 fixes and apply the additional production backports.
COPY --from=agent-native-sources /openssl.tar.gz /tmp/openssl.tar.gz
COPY deployment/vendors/agent-native-20261122/CVE-2026-*.patch /patches/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential perl && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN echo 'e0fb736a48c483b655997d3c4c18a8c9991da03923af1ea4abd4ee883d93c59b  /tmp/openssl.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/openssl.tar.gz --strip-components=1 \
    && patch -p1 --fuzz=0 < /patches/CVE-2026-84786.patch \
    && patch -p1 --fuzz=0 < /patches/CVE-2026-95690.patch \
    && patch -p1 --fuzz=0 < /patches/CVE-2026-121482.patch \
    && ./Configure linux-x86_64 shared no-tests --prefix=/usr --libdir=lib/x86_64-linux-gnu --openssldir=/usr/lib/ssl \
    && SOURCE_DATE_EPOCH=1794873600 make -j4 build_libs \
    && strip --strip-unneeded libcrypto.so.3 libssl.so.3 \
    && install -m 0644 -D libcrypto.so.3 /out/usr/lib/x86_64-linux-gnu/libcrypto.so.3 \
    && install -m 0644 -D libssl.so.3 /out/usr/lib/x86_64-linux-gnu/libssl.so.3 \
    && install -m 0644 -D LICENSE.txt /out/usr/share/licenses/openssl-source/LICENSE.txt \
    && mkdir -p /out/usr/share/deepresearch \
    && printf '%s\n' f3acb0a3328046741479482c51cec815839e1b05 > /out/usr/share/deepresearch/openssl-revision \
    && cd /out && sha256sum usr/lib/x86_64-linux-gnu/libcrypto.so.3 usr/lib/x86_64-linux-gnu/libssl.so.3 > usr/share/deepresearch/openssl-libraries.sha256
# DockerHub3.11.18 tags were not published at verification time. Build the
# official signed CPython release using its pinned upstream slim recipe.
FROM debian:trixie-slim@sha256:38a76d01668772e381ad2826d876627c89e7133e2f8a0f5d567306798b0f2a16 AS python-security
# CPython embeds its compiler date in the shared library. Pin it for the runtime byte check.
ARG PYTHON_BUILD_JOBS=4
ARG SOURCE_DATE_EPOCH=1794873600
ENV PATH=/usr/local/bin:$PATH LANG=C.UTF-8 \
    PYTHON_RELEASE_FINGERPRINT=A035C8C19219BA821ECEA86B64E628F8D684696D \
    PYTHON_VERSION=3.11.18 \
    PYTHON_SHA256=9120e0a653868f6e3698d36cdd99e5a58903ec9469e448f21d98f7b16d8fa290
ADD --checksum=sha256:9120e0a653868f6e3698d36cdd99e5a58903ec9469e448f21d98f7b16d8fa290 https://www.python.org/ftp/python/3.11.18/Python-3.11.18.tar.xz /tmp/Python.tar.xz
COPY deployment/vendors/python311/release-key.asc /tmp/python-release-key.asc
COPY deployment/vendors/python311/build-python.sh /tmp/build-python.sh
RUN sh /tmp/build-python.sh
FROM python-security AS builder
COPY --from=uv /uv /usr/local/bin/uv
# Debian 13 has no fixed SQLite package yet. This official Debian package keeps
# the same SONAME and needs libc >=2.38 (the pinned Debian 13 runtime has 2.41).
ADD --checksum=sha256:7789bf0b2338b781de388d83d7c473f9386c9ef638af110b5c1b3d1debb36f00 https://deb.debian.org/debian/pool/main/s/sqlite3/libsqlite3-0_3.54.1-2_amd64.deb /tmp/sqlite.deb
ADD --checksum=sha256:8506ce550a81ac14b047608e0df121037d9cbb10c3cc28446f68cf8c1d9d31ce https://deb.debian.org/debian/pool/main/z/zlib/zlib1g_1.3.dfsg+really1.3.2-4_amd64.deb /tmp/zlib.deb
RUN dpkg -i /tmp/sqlite.deb /tmp/zlib.deb \
    && test "$(dpkg-query -W -f='${Version}' libsqlite3-0)" = '3.54.1-2' \
    && test "$(dpkg-query -W -f='${Version}' zlib1g)" = '1:1.3.dfsg+really1.3.2-4' \
    && python3 -c 'import sqlite3,zlib; assert sqlite3.sqlite_version == "3.54.1"; assert zlib.ZLIB_RUNTIME_VERSION == "1.3.2"'
RUN apt-get update && apt-get upgrade -y \
    && cd /tmp && apt-get download ca-certificates=20250419+deb13u1 \
    && echo 'c10585ac22c7f617a73d394eea29ba71d0356f350ae973b19043d1b017f80f54  ca-certificates_20250419+deb13u1_all.deb' | sha256sum -c - \
    && apt-get install -y --no-install-recommends /tmp/ca-certificates_20250419+deb13u1_all.deb \
    && update-ca-certificates --fresh && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip setuptools wheel
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-python-downloads --link-mode=copy \
    && mkdir /data && chown 10001:10001 /data
COPY deployment/vendors/python311/source-lock.json /usr/local/share/deepresearch-python-source.json
COPY scripts/prepare_python_runtime.py /tmp/prepare_python_runtime.py
COPY scripts/prepare_runtime_trust.py /tmp/prepare_runtime_trust.py
# The interpreter is already linked. Its development startup object stays out of the runtime.
RUN python /tmp/prepare_python_runtime.py \
    && rm /usr/local/bin/uv /usr/local/lib/python3.11/config-*/python.o

FROM builder AS runtime-package
COPY --from=runtime-base / /runtime-root/
COPY --from=openssl-security /out/ /runtime-root/
# The Debian 13 base still carries affected glibc 2.41. Keep the compiler and
# build environment unchanged; stage the complete matched official runtime.
ADD --checksum=sha256:5c71715c51103beb4fbba9fe9f8dff604af6610dfa71c70bff342f54a7860803 https://deb.debian.org/debian/pool/main/g/glibc/libc6_2.43-7_amd64.deb /tmp/glibc/libc6_2.43-7_amd64.deb
ADD --checksum=sha256:8abae40b5e2bfca58fbc838329b48f525c98463e930b8d3ed075d0c9e960a57a https://deb.debian.org/debian/pool/main/g/glibc/libc-gconv-modules-extra_2.43-7_amd64.deb /tmp/glibc/libc-gconv-modules-extra_2.43-7_amd64.deb
COPY scripts/stage_glibc_runtime.py /tmp/stage_glibc_runtime.py
RUN python /tmp/stage_glibc_runtime.py --runtime-root /runtime-root --archives /tmp/glibc
COPY --from=glibc-security /out/ /runtime-root/
COPY --from=stdcxx-security /out/ /runtime-root/
# Replace complete trust trees before flattening: an overlay on runtime-base
# alone would retain obsolete named/hashed certificate files that were removed.
RUN python /tmp/prepare_runtime_trust.py --runtime-root /runtime-root --kind ca

FROM scratch
COPY --from=runtime-package /runtime-root/ /
COPY --from=builder /usr/local /usr/local
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /runtime/ /
COPY deployment/security/agent-glibc-runtime-20261110.json /usr/local/share/deepresearch-glibc-source.json
COPY deployment/vendors/agent-native-20261122/source.lock.json /usr/local/share/deepresearch-agent-native-source.json
COPY --from=builder --chown=10001:10001 /data /data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt LD_LIBRARY_PATH=/usr/local/lib DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY backend ./backend
COPY scripts/migrate_legacy_sqlite.py ./scripts/migrate_legacy_sqlite.py
COPY scripts/alert_relay_service.py ./scripts/alert_relay_service.py
COPY scripts/bootstrap_services.py scripts/migrate_services.py scripts/ops_metrics.py scripts/rebuild_lexical_index.py ./scripts/
COPY scripts/embedding_migration.py scripts/openai_smoke.py ./scripts/
USER 0:0
RUN ["python", "-c", "import pathlib,hashlib,json; root=pathlib.Path('/app'); manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}; (root/'source-fingerprint.json').write_text(json.dumps(manifest,sort_keys=True),encoding='utf-8')"]
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["uvicorn", "backend.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
