"""Offline checks of the resolved cloud Compose; never prints resolved secrets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deployment/compose/compose.cloud.yaml"


def inspect_configuration(config: dict, *, allow_legacy_embedding_endpoint: bool = False) -> dict:
    checks = []

    def check(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "requirement": detail})

    def https(value):
        url = urlsplit(value)
        return (url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
                and not url.query and not url.fragment)

    def configured(value):
        return bool(value) and "REPLACE_" not in value and "example.com" not in value

    def readonly_mount(service, filename):
        path = PurePosixPath(filename)
        if not path.is_absolute() or '..' in path.parts:
            return False
        for mount in services.get(service, {}).get('volumes', []):
            if not isinstance(mount, dict) or not mount.get('read_only'):
                continue
            target = PurePosixPath(mount.get('target', ''))
            if target == path or target in path.parents:
                return True
        return False

    services = config.get("services", {})
    required = {"edge", "agent-gateway", "web", "backend", "agent", "worker", "document-worker", "migrate", "token-init"}
    check("application_services", required <= services.keys(), "All application and initialization services must exist")
    check("external_data_services", not {"postgres", "redis", "milvus", "minio", "bootstrap-db"} & services.keys(),
          "Use the standalone cloud manifest with external data services")
    public_url = config.get("x-public-url", "")
    check("public_https", https(public_url) and configured(public_url) and urlsplit(public_url).path in {"", "/"},
          "Provide the real HTTPS application origin; subpath deployment is not supported")
    environments = {name: service.get("environment", {}) for name, service in services.items()}
    runtime_names = ("agent", "worker", "document-worker")
    python = environments.get("agent", {})
    java = environments.get("backend", {})
    for name in runtime_names:
        env = environments.get(name, {})
        check(name + "_controls", env.get("AUTH_MODE") == "oidc" and env.get("DEMO_MODE") == "false"
              and env.get("QUEUE_BACKEND") == "redis" and env.get("DOCUMENT_SCAN_MODE") == "clamav"
              and env.get("OBJECT_STORE_BACKEND") == "s3" and env.get("CHECKPOINT_SCHEMA_MANAGED") == "true",
              "OIDC, real providers, persistent queue, scanning, S3 and migration-owned checkpoints are mandatory")
        check(name + "_credential_isolation", not {"BOOTSTRAP_DATABASE_URL", "MIGRATOR_DATABASE_URL", "JAVA_DB_PASSWORD"} & env.keys(),
              "Do not inject administrator, migrator or Java credentials into Python runtimes")
        shared = ("DATABASE_URL", "REDIS_URL", "MILVUS_URI", "MILVUS_DB_NAME", "OBJECT_STORE_ENDPOINT",
                  "OBJECT_STORE_BUCKET", "OBJECT_STORE_REGION", "CLAMAV_HOST", "CLAMAV_PORT", "OIDC_CA_CERTS",
                  "LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL_ID", "VISION_MODEL_ID", "LLM_REASONING_EFFORT",
                  "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIMENSION", "SEND_EMBEDDING_DIMENSIONS")
        check(name + "_data_alignment", all(env.get(key) == python.get(key) for key in shared),
              "API and workers must share data endpoints, OIDC trust and model/index protocol configuration")
    check("java_oidc", java.get("AUTH_MODE") == "oidc", "Java must verify OIDC tokens")
    check("application_replicas", all(services.get(name, {}).get("deploy", {}).get("replicas", 1) >= 2
                                     for name in ("web", "backend", *runtime_names)),
          "Use at least two replicas for each application role")
    check("shared_control", java.get("REDIS_URL") == python.get("REDIS_URL") and bool(java.get("DEPLOYMENT_ID"))
          and java.get("PLATFORM_ACTIVE_RUN_LIMIT") == python.get("PLATFORM_ACTIVE_RUN_LIMIT") == "5"
          and all(environments.get(name, {}).get("MAX_QUEUED_RUNS") == "20" for name in (*runtime_names, "backend"))
          and all(environments.get(name, {}).get("WORKSPACE_CONCURRENT_RUN_LIMIT") == "2" for name in (*runtime_names, "backend")),
          "All replicas share Redis and the database; platform/workspace limits are 5 running, 20 queued and 2 running per workspace")
    check("worker_lifecycle", all("backend.worker" in str(services.get(name, {}).get("command", ""))
          and "--ready" in str(services.get(name, {}).get("healthcheck", {}).get("test", ""))
          for name in ("worker", "document-worker")), "Workers must use the draining runner and readiness probe")
    check("browser_secret_isolation", set(environments.get("web", {})) <= {"OIDC_CONNECT_ORIGINS"},
          "Browser container receives only public identity origins")
    check("identity_alignment", all(environments.get(name, {}).get("OIDC_ISSUER") == python.get("OIDC_ISSUER")
                                   and environments.get(name, {}).get("OIDC_AUDIENCE") == python.get("OIDC_AUDIENCE")
                                   and environments.get(name, {}).get("OIDC_JWKS_URL") == python.get("OIDC_JWKS_URL")
                                   for name in (*runtime_names, "backend")),
          "All runtimes must use the same issuer, JWKS endpoint and API audience")
    operators = java.get('PLATFORM_OPERATOR_SUBJECTS', '')
    check('platform_operators', any(part.strip() for part in operators.split(',')) and all(
          environments.get(name, {}).get('PLATFORM_OPERATOR_SUBJECTS') == operators for name in runtime_names),
          'Configure the same explicit platform-operator subjects in Java and Python')
    try:
        distribution = json.loads(java.get('SPRING_APPLICATION_JSON', '{}')).get('management', {}).get('metrics', {}).get('distribution', {})
        histogram = distribution.get('percentiles-histogram', {}).get('http.server.requests') is True
    except (ValueError, AttributeError):
        histogram = False
    check('java_request_histogram', histogram,
          'Enable the request histogram through structured SPRING_APPLICATION_JSON; ambiguous flattened map variables can prevent Boot4 startup')
    for key in ("OIDC_ISSUER", "OIDC_JWKS_URL"):
        check(key.lower(), https(python.get(key, "")) and configured(python.get(key, "")),
              "Provide an actual HTTPS identity endpoint; network access is checked separately")
    origins = environments.get("web", {}).get("OIDC_CONNECT_ORIGINS", "").split()
    check("oidc_csp", bool(origins) and all(https(origin) and configured(origin)
          and urlsplit(origin).path in {"", "/"} and "*" not in origin for origin in origins)
          and urlsplit(python.get("OIDC_ISSUER", "")).netloc in {urlsplit(origin).netloc for origin in origins},
          "CSP must allow exact HTTPS identity origins including discovery and token services")
    dsns = [("python_database", python.get("DATABASE_URL", ""), "postgresql", "dr_python"),
            ("migrator_database", environments.get("migrate", {}).get("DATABASE_URL", ""), "postgresql+psycopg", "dr_migrator"),
            ("java_database", java.get("SPRING_DATASOURCE_URL", "").removeprefix("jdbc:"), "postgresql", None)]
    for name, value, scheme, role in dsns:
        url = urlsplit(value)
        check(name, url.scheme == scheme and bool(url.hostname) and configured(value)
              and parse_qs(url.query).get("sslmode") == ["verify-full"]
              and (role is None or (url.username == role and bool(url.password))),
              "Use the documented dedicated role, DSN driver and sslmode=verify-full")
        ca = parse_qs(url.query).get('sslrootcert', [''])[0]
        owner = 'backend' if name == 'java_database' else 'migrate' if name == 'migrator_database' else 'agent'
        check(name + '_ca_mount', bool(ca) and readonly_mount(owner, ca),
              'Database verification CA must reference a read-only certificate mount')
    databases = [urlsplit(value) for _, value, _, _ in dsns]
    check("database_alignment", len({(url.hostname, url.port or 5432, url.path) for url in databases}) == 1,
          "The three database roles must connect to the same application database")
    passwords = [unquote(databases[0].password or ""), unquote(databases[1].password or ""),
                 java.get("SPRING_DATASOURCE_PASSWORD", "")]
    check("database_password_isolation", all(passwords) and len(set(passwords)) == 3,
          "Python, migrator and Java passwords must be distinct")
    check("java_database_role", java.get("SPRING_DATASOURCE_USERNAME") == "dr_java"
          and configured(java.get("SPRING_DATASOURCE_PASSWORD", "")), "Java must use its own dr_java credential")
    redis = urlsplit(python.get("REDIS_URL", ""))
    check("redis_tls", redis.scheme == "rediss" and bool(redis.hostname) and bool(redis.password)
          and configured(python.get("REDIS_URL", "")) and not redis.query,
          "Use authenticated rediss:// with certificate and hostname verification; no ignored DSN options")
    for name in (*runtime_names, 'backend'):
        env = environments.get(name, {})
        paths = ('REDIS_SSL_CA_CERTS',) if name == 'backend' else (
            'REDIS_SSL_CA_CERTS', 'MILVUS_SERVER_PEM_PATH', 'OIDC_CA_CERTS', 'OTEL_EXPORTER_OTLP_CERTIFICATE',
            'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE')
        database_ca = parse_qs(urlsplit(env.get('DATABASE_URL', '')).query).get('sslrootcert', [''])[0]
        check(name + '_certificate_mounts', all(not env.get(key) or readonly_mount(name, env[key]) for key in paths)
              and (name == 'backend' or (bool(database_ca) and readonly_mount(name, database_ca))),
              'Configured private CA and workload-identity files must be mounted read-only')
        if name in runtime_names:
            bundle = env.get('AWS_CA_BUNDLE', '')
            check(name + '_s3_ca_mount', not bundle or bundle == '/etc/ssl/certs/ca-certificates.crt'
                  or readonly_mount(name, bundle), 'S3 CA must use the image trust bundle or a read-only mounted file')
            check(name + '_web_identity', bool(env.get('AWS_ROLE_ARN')) == bool(env.get('AWS_WEB_IDENTITY_TOKEN_FILE')),
                  'Web identity requires both the role ARN and the projected token file')
    try:
        options = shlex.split(java.get('JAVA_TOOL_OPTIONS', ''))
        stores = [option.split('=', 1)[1] for option in options if option.startswith('-Djavax.net.ssl.trustStore=')]
        java_store_valid = len(stores) == 1 and readonly_mount('backend', stores[0])
    except ValueError:
        java_store_valid = False
    check('oidc_private_trust', not python.get('OIDC_CA_CERTS') or java_store_valid,
          'A private OIDC CA requires the matching Java truststore on a read-only mount')
    check("milvus_tls_auth", https(python.get("MILVUS_URI", "")) and configured(python.get("MILVUS_URI", ""))
          and configured(python.get("MILVUS_TOKEN", "")), "Provide the actual HTTPS Milvus endpoint and token")
    endpoint = python.get("OBJECT_STORE_ENDPOINT", "")
    access, secret = python.get("OBJECT_STORE_ACCESS_KEY", ""), python.get("OBJECT_STORE_SECRET_KEY", "")
    check("storage_tls", not endpoint or (https(endpoint) and configured(endpoint)), "Custom S3 endpoints must use HTTPS")
    check("storage_credentials", bool(access) == bool(secret) and (not access or (configured(access) and configured(secret)))
          and (not python.get("OBJECT_STORE_SESSION_TOKEN") or bool(access)),
          "Supply both S3 keys, or leave both empty to use the SDK workload-identity chain")
    check("storage_bucket", configured(python.get("OBJECT_STORE_BUCKET", ""))
          and configured(python.get("OBJECT_STORE_REGION", ""))
          and python.get("OBJECT_STORE_AUTO_CREATE_BUCKET") == "false",
          "Provision the bucket, region and permissions before starting; application cannot create buckets")
    check("storage_encryption", python.get("OBJECT_STORE_SSE", "") in {"", "AES256", "aws:kms"}
          and (not python.get("OBJECT_STORE_KMS_KEY_ID") or python.get("OBJECT_STORE_SSE") == "aws:kms"),
          "KMS key configuration requires aws:kms; otherwise use the provisioned bucket encryption policy")
    check("scanner_endpoint", configured(python.get("CLAMAV_HOST", "")), "Supply a ClamAV endpoint reachable only over a private network")
    for key in ("LLM_BASE_URL", "EMBEDDING_BASE_URL"):
        check(key.lower(), https(python.get(key, "")) and configured(python.get(key, "")), "Provider endpoints must use HTTPS")
    for key in ("LLM_API_KEY", "EMBEDDING_API_KEY", "LLM_MODEL_ID", "EMBEDDING_MODEL"):
        check(key.lower(), configured(python.get(key, "")), "Configure the actual provider credential or model")
    check('llm_protocol', python.get('LLM_PROVIDER') in {'openai', 'compatible'},
          'Select the official OpenAI adapter or explicitly declare the compatible provider protocol')
    search = python.get("WEB_SEARCH_PROVIDER")
    check("search_configuration", search in {"openai", "deepseek", "auto"} or
          (search == "bocha" and configured(python.get("BOCHA_API_KEY", "")) and https(python.get("BOCHA_BASE_URL", ""))),
          "Configure a supported search provider; actual protocol/model compatibility needs a live acceptance test")
    if python.get("LLM_PROVIDER") == "openai":
        check("openai_credential_destination", python.get("LLM_BASE_URL") == "https://api.openai.com/v1",
              "The default OpenAI project key must only be sent to the official API")
        official_embedding = python.get('EMBEDDING_BASE_URL') == 'https://api.openai.com/v1'
        separate_legacy_key = all(environments.get(name, {}).get('EMBEDDING_API_KEY') !=
                                 environments.get(name, {}).get('LLM_API_KEY') for name in runtime_names)
        check('openai_embedding_destination', official_embedding or (allow_legacy_embedding_endpoint and separate_legacy_key),
              'Official OpenAI embeddings are the default; retaining a legacy endpoint requires an explicit preflight exception and a separate credential')
    check("reranker_configuration", python.get("RERANKER_ENABLED") == "false" or
          (configured(python.get("RERANKER_API_KEY", "")) and https(python.get("RERANKER_URL", ""))),
          "Disable reranking or supply its HTTPS endpoint and credential")
    telemetry = python.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    check("telemetry_tls", not telemetry or (https(telemetry) and configured(telemetry)),
          "External OTLP/gRPC collectors must use HTTPS; configure authentication through SDK headers")
    check("private_ports", all(name == "edge" and port.get("host_ip") in {"127.0.0.1", "::1"}
                              for name, service in services.items() for port in service.get("ports", [])),
          "Only edge may publish a loopback port; place the TLS ingress on this host or use an explicitly private network")
    return {"configuration_valid": all(item["passed"] for item in checks), "checks": checks,
            "live_connectivity": "not_checked", "production_approved": False,
            "remaining_acceptance": ["TLS ingress and identity login/refresh/logout", "database grants and restore drill",
                                     "object-store workload credentials and CRUD", "queue and worker recovery",
                                     "model/search quality and capacity", "alert delivery and external availability monitor"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.cloud")
    parser.add_argument("--output", type=Path)
    parser.add_argument('--allow-legacy-embedding-endpoint', action='store_true',
                        help='Explicitly retain a historical embedding endpoint with its separate legacy credential; does not authorize migration')
    args = parser.parse_args()
    try:
        result = subprocess.run(["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(args.env_file.resolve()),
                                 "-f", str(COMPOSE), "config", "--format", "json"],
                                cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError("Configuration rendering failed")
        report = inspect_configuration(json.loads(result.stdout),
                                       allow_legacy_embedding_endpoint=args.allow_legacy_embedding_endpoint)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report = {"configuration_valid": False, "production_approved": False,
                  "error": "Cloud Compose rendering/check failed; check required fields locally. Private output withheld."}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    raise SystemExit(0 if report["configuration_valid"] else 1)


if __name__ == "__main__":
    main()
