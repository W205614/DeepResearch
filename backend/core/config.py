"""Environment configuration. Never serialize Settings into an HTTP response."""
import json
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True, hide_input_in_errors=True)
    llm_provider: Literal["openai", "compatible"] = "openai"
    llm_model_id: str = Field("gpt-6.1-sol", validation_alias=AliasChoices("LLM_MODEL_ID", "LLM_MODEL"))
    vision_model_id: str = "gpt-6.1-sol"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_api_key: SecretStr = SecretStr("")
    llm_extra_body: str = '{}'
    embedding_model: str = "text-embedding-3-large"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_dimension: int = Field(3072, ge=2, le=32768)
    send_embedding_dimensions: bool = True
    web_search_provider: Literal["openai", "deepseek", "bocha", "auto"] = "openai"
    bocha_base_url: str = "https://api.bochaai.com/v1"
    bocha_api_key: SecretStr = SecretStr("")
    auth_mode: Literal["oidc", "development"] = "oidc"
    oidc_issuer: str = ""
    oidc_audience: str = "deepresearch-api"
    oidc_jwks_url: str = ""
    oidc_ca_certs: Path | None = None
    oidc_client_id: str = "deepresearch-web"
    oidc_scopes: str = "openid profile email"
    oidc_registration_enabled: bool = True
    development_jwt_secret: SecretStr = SecretStr("development-only-secret-must-be-32-bytes")
    database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_ssl_ca_certs: str = ""
    queue_backend: Literal["redis", "local"] = "redis"
    max_job_retries: int = Field(2, ge=0, le=10)
    otel_exporter_otlp_endpoint: str = ""
    feishu_webhook_url: SecretStr = SecretStr("")
    alert_relay_token: SecretStr = SecretStr("")
    alert_relay_token_file: Path | None = None
    internal_service_token: SecretStr = SecretStr("")
    internal_service_token_file: Path | None = None
    workspace_concurrent_run_limit: int = Field(2, ge=1, le=20)
    document_scan_mode: Literal["disabled", "clamav"] = "clamav"
    clamav_host: str = "clamav"
    clamav_port: int = Field(3310, ge=1, le=65535)
    object_store_backend: Literal["filesystem", "s3"] = "filesystem"
    object_store_endpoint: str = ""
    object_store_bucket: str = "deepresearch-documents"
    object_store_access_key: SecretStr = SecretStr("")
    object_store_secret_key: SecretStr = SecretStr("")
    object_store_session_token: SecretStr = SecretStr("")
    object_store_region: str = "us-east-1"
    object_store_addressing_style: Literal["auto", "path", "virtual"] = "auto"
    object_store_auto_create_bucket: bool = True
    object_store_sse: Literal["", "AES256", "aws:kms"] = ""
    object_store_kms_key_id: str = ""
    source_trust_overrides: str = '{}'
    web_cache_ttl_hours: int = Field(168, ge=1, le=24 * 90)
    auto_save_semantic_memory: bool = False
    data_dir: Path = Path("data")
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr = SecretStr("")
    milvus_db_name: str = "default"
    milvus_server_pem_path: str = ""
    vector_collection_prefix: str = "dr"
    demo_mode: bool = False
    rag_min_vector_score: float = Field(0.2, ge=0, le=1)
    max_reflection_rounds: int = Field(2, ge=0, le=5)
    max_search_calls: int = Field(12, ge=1, le=30)
    web_results_per_query: int = Field(8, ge=3, le=15)
    max_web_candidates: int = Field(12, ge=6, le=60)
    web_search_concurrency: int = Field(3, ge=1, le=8)
    web_fetch_concurrency: int = Field(6, ge=1, le=12)
    web_search_timeout_seconds: float = Field(20, ge=1, le=120)
    web_fetch_timeout_seconds: float = Field(8, ge=1, le=60)
    max_run_seconds: int = Field(600, ge=10, le=3600)
    max_concurrent_runs: int = Field(3, ge=1, le=8)
    platform_active_run_limit: int = Field(5, ge=1, le=100)
    research_worker_max_jobs: int = Field(3, ge=1, le=8)
    worker_health_file: Path = Path("/tmp/deepresearch-worker-health.json")
    worker_drain_seconds: int = Field(30, ge=1, le=300)
    max_llm_concurrency: int = Field(3, ge=1, le=8)
    task_log_level: str = "INFO"
    semantic_memory_min_score: float = Field(0.35, ge=-1, le=1)
    conversation_recent_runs: int = Field(6, ge=1, le=12)
    conversation_turn_limit: int = Field(30, ge=5, le=200)
    request_timeout_seconds: float = Field(90, ge=1, le=300)
    max_run_total_seconds: int = Field(1200, ge=10, le=86400)
    max_run_call_attempts: int = Field(80, ge=1, le=500)
    max_run_reserved_tokens: int = Field(2000000, ge=1000, le=20000000)
    max_queued_runs: int = Field(20, ge=1, le=1000)
    llm_context_tokens: int = Field(128000, ge=4096)
    vision_context_tokens: int = Field(128000, ge=4096)
    vision_image_token_reserve: int = Field(16000, ge=1024)
    context_safety_tokens: int = Field(2048, ge=256)
    provider_cooldown_seconds: float = Field(30, ge=1, le=300)
    rag_vector_timeout_seconds: float = Field(3, ge=0.01, le=30)
    rag_max_corpus_chunks: int = Field(4000, ge=1, le=20000)
    rag_corpus_cache_users: int = Field(4, ge=1, le=32)
    reranker_enabled: bool = True
    reranker_url: str = "https://api.siliconflow.cn/v1/rerank"
    reranker_api_key: SecretStr = SecretStr("")
    reranker_model: str = "Pro/BAAI/bge-reranker-v2-m3"
    reranker_document_format: str = "strings"
    reranker_timeout_seconds: float = Field(2, ge=0.1, le=30)
    reranker_candidate_limit: int = Field(20, ge=2, le=100)
    allow_internal_model_processing: bool = False
    parser_memory_mb: int = Field(512, ge=256, le=2048)
    parser_cpu_seconds: int = Field(20, ge=1, le=120)
    parser_wall_seconds: float = Field(30, ge=0.05, le=180)
    parser_output_mb: int = Field(36, ge=1, le=64)
    checkpoint_schema_managed: bool = False
    retention_cleanup_batch_size: int = Field(100, ge=1, le=1000)
    retention_cache_days: int = Field(7, ge=1, le=90)
    retention_archive_after_days: int = Field(0, ge=0, le=3650)
    rag_small_corpus_chunks: int = Field(1000, ge=1, le=4000)
    rag_db_candidate_limit: int = Field(128, ge=20, le=512)
    workspace_document_limit: int = Field(500, ge=1, le=10000)
    workspace_chunk_limit: int = Field(50000, ge=400, le=1000000)
    # Operations-only hard caps; workspace administrators cannot raise them.
    platform_operator_subjects: str = ""
    provider_probe_cooldown_seconds: int = Field(300, ge=30, le=86400)
    probe_period_calls: int = Field(20, ge=1, le=1000)
    probe_period_tokens: int = Field(200000, ge=1000, le=20000000)
    usage_period_seconds: int = Field(86400, ge=60, le=2678400)
    platform_period_calls: int = Field(1000, ge=1, le=1000000)
    platform_period_tokens: int = Field(20000000, ge=1000, le=2000000000)
    subject_period_calls: int = Field(200, ge=1, le=1000000)
    subject_period_tokens: int = Field(4000000, ge=1000, le=2000000000)
    workspace_period_calls: int = Field(400, ge=1, le=1000000)
    workspace_period_tokens: int = Field(8000000, ge=1000, le=2000000000)
    account_period_calls: int = Field(1000, ge=1, le=1000000)
    account_period_tokens: int = Field(20000000, ge=1000, le=2000000000)

    @field_validator("object_store_endpoint")
    @classmethod
    def validate_storage_endpoint(cls, value: str) -> str:
        if not value:
            return ""
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError("OBJECT_STORE_ENDPOINT must be an HTTP(S) address without credentials or query")
        return value.rstrip("/")

    @field_validator("oidc_ca_certs", mode="before")
    @classmethod
    def validate_oidc_ca(cls, value):
        if value in (None, ""):
            return None
        path = Path(value)
        if not path.is_file():
            raise ValueError("OIDC_CA_CERTS must point to an existing trusted CA file")
        return path

    @model_validator(mode="after")
    def validate_storage_credentials(self):
        if self.object_store_backend != "s3":
            return self
        access = bool(self.object_store_access_key.get_secret_value())
        secret = bool(self.object_store_secret_key.get_secret_value())
        if access != secret:
            raise ValueError("Object-store access and secret keys must be supplied together")
        if self.object_store_session_token.get_secret_value() and not access:
            raise ValueError("An explicit object-store session token requires access and secret keys")
        if self.object_store_kms_key_id and self.object_store_sse != "aws:kms":
            raise ValueError("OBJECT_STORE_KMS_KEY_ID requires OBJECT_STORE_SSE=aws:kms")
        return self

    @property
    def operator_subjects(self) -> frozenset[str]:
        return frozenset(value.strip() for value in self.platform_operator_subjects.split(",") if value.strip())

    @field_validator("llm_base_url", "embedding_base_url", "bocha_base_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query:
            raise ValueError("API base URL must be an HTTP(S) address without credentials or query")
        return value.rstrip("/")

    @field_validator("reranker_url")
    @classmethod
    def validate_optional_api_url(cls, value: str) -> str:
        if not value:
            return ""
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError("RERANKER_URL must be an HTTP(S) address without credentials or query")
        return value.rstrip("/")

    @field_validator("reranker_document_format")
    @classmethod
    def validate_reranker_document_format(cls, value: str) -> str:
        if value not in {"objects", "strings"}:
            raise ValueError("RERANKER_DOCUMENT_FORMAT must be objects or strings")
        return value

    @field_validator("llm_extra_body")
    @classmethod
    def validate_extra(cls, value: str) -> str:
        value = value or '{}'
        body = json.loads(value)
        if not isinstance(body, dict) or set(body) & {"model", "messages", "stream", "max_tokens", "max_completion_tokens", "max_output_tokens"}:
            raise ValueError("LLM_EXTRA_BODY must be an object without core request overrides")
        return value

    @field_validator("source_trust_overrides")
    @classmethod
    def validate_source_trust_overrides(cls, value: str) -> str:
        parsed = json.loads(value or '{}')
        if not isinstance(parsed, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in parsed.items()):
            raise ValueError("SOURCE_TRUST_OVERRIDES must be a JSON object of domain labels")
        return json.dumps({k.lower().lstrip('.'): v[:40] for k, v in parsed.items()})

    def trusted_source_label(self, host: str) -> str:
        for domain, label in json.loads(self.source_trust_overrides).items():
            if host == domain or host.endswith("." + domain):
                return label
        return ""

    def missing(self) -> list[str]:
        if self.demo_mode:
            return []
        required = [
            ("LLM_API_KEY", self.llm_api_key), ("EMBEDDING_API_KEY", self.embedding_api_key),
        ]
        if self.web_search_provider == "bocha":
            required.append(("BOCHA_API_KEY", self.bocha_api_key))
        return [name for name, val in required if not val.get_secret_value()]

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        (self.data_dir / "quarantine").mkdir(exist_ok=True)


def endpoint(base: str, path: str) -> str:
    """Allow a root provider URL (DeepSeek) as well as a /v1 base."""
    return base.rstrip("/") + "/" + path.lstrip("/")
