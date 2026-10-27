FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
# DockerHub3.11.18 tags were not published at verification time. Build the
# official signed CPython release using its pinned upstream slim recipe.
FROM debian:trixie-slim@sha256:38a76d01668772e381ad2826d876627c89e7133e2f8a0f5d567306798b0f2a16 AS python-security
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
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip setuptools wheel
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-python-downloads --link-mode=copy \
    && mkdir /data && chown 10001:10001 /data
COPY deployment/vendors/python311/source-lock.json /usr/local/share/deepresearch-python-source.json
COPY scripts/prepare_python_runtime.py /tmp/prepare_python_runtime.py
RUN python /tmp/prepare_python_runtime.py

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2
COPY --from=builder /usr/local /usr/local
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /runtime/ /
COPY --from=builder --chown=10001:10001 /data /data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:$PATH" LD_LIBRARY_PATH=/usr/local/lib DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY backend ./backend
COPY scripts/migrate_legacy_sqlite.py ./scripts/migrate_legacy_sqlite.py
COPY scripts/alert_relay_service.py ./scripts/alert_relay_service.py
COPY scripts/bootstrap_services.py scripts/migrate_services.py scripts/ops_metrics.py scripts/rebuild_lexical_index.py ./scripts/
USER 0:0
RUN ["python", "-c", "import pathlib,hashlib,json; root=pathlib.Path('/app'); manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}; (root/'source-fingerprint.json').write_text(json.dumps(manifest,sort_keys=True),encoding='utf-8')"]
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "backend.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
