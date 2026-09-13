"""Environment configuration. Never serialize Settings into an HTTP response."""
import json
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)
    llm_model_id: str = Field("deepseek-v4-flash", validation_alias=AliasChoices("LLM_MODEL_ID", "LLM_MODEL"))
    vision_model_id: str = "deepseek-v4-flash-vision-exp"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_extra_body: str = '{}'
    embedding_model: str = "text-embedding-3-large"
    embedding_base_url: str = "https://api.openai-proxy.org/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_dimension: int = Field(3072, ge=2, le=32768)
    send_embedding_dimensions: bool = False
    web_search_provider: Literal["deepseek", "bocha", "auto"] = "deepseek"
    bocha_base_url: str = "https://api.bochaai.com/v1"
    bocha_api_key: SecretStr = SecretStr("")
    auth_mode: Literal["oidc", "development"] = "oidc"
    oidc_issuer: str = ""
    oidc_audience: str = "deepresearch-api"
    oidc_jwks_url: str = ""
    development_jwt_secret: SecretStr = SecretStr("development-only-secret-must-be-32-bytes")
    database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    queue_backend: Literal["redis", "local"] = "redis"
    max_job_retries: int = Field(2, ge=0, le=10)
    otel_exporter_otlp_endpoint: str = ""
    feishu_webhook_url: SecretStr = SecretStr("")
    alert_relay_token: SecretStr = SecretStr("")
    alert_relay_token_file: Path | None = None
    workspace_concurrent_run_limit: int = Field(2, ge=1, le=20)
    document_scan_mode: Literal["disabled", "clamav"] = "clamav"
    clamav_host: str = "clamav"
    clamav_port: int = Field(3310, ge=1, le=65535)
    object_store_backend: Literal["filesystem", "s3"] = "filesystem"
    object_store_endpoint: str = ""
    object_store_bucket: str = "deepresearch-documents"
    object_store_access_key: SecretStr = SecretStr("")
    object_store_secret_key: SecretStr = SecretStr("")
    source_trust_overrides: str = '{}'
    web_cache_ttl_hours: int = Field(168, ge=1, le=24 * 90)
    auto_save_semantic_memory: bool = False
    data_dir: Path = Path("data")
    milvus_uri: str = "http://127.0.0.1:19530"
    vector_collection_prefix: str = "dr"
    demo_mode: bool = False
    rag_min_vector_score: float = Field(0.2, ge=0, le=1)
    max_reflection_rounds: int = Field(2, ge=0, le=5)
    max_search_calls: int = Field(12, ge=1, le=30)
    web_results_per_query: int = Field(8, ge=3, le=15)
    max_web_candidates: int = Field(24, ge=6, le=60)
    web_search_concurrency: int = Field(3, ge=1, le=8)
    web_fetch_concurrency: int = Field(6, ge=1, le=12)
    max_run_seconds: int = Field(600, ge=10, le=3600)
    max_concurrent_runs: int = Field(2, ge=1, le=8)
    max_llm_concurrency: int = Field(3, ge=1, le=8)
    task_log_level: str = "INFO"
    semantic_memory_min_score: float = Field(0.35, ge=-1, le=1)
    conversation_recent_runs: int = Field(6, ge=1, le=12)
    conversation_turn_limit: int = Field(30, ge=5, le=200)
    request_timeout_seconds: float = Field(90, ge=1, le=300)

    @field_validator("llm_base_url", "embedding_base_url", "bocha_base_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query:
            raise ValueError("API base URL must be an HTTP(S) address without credentials or query")
        return value.rstrip("/")

    @field_validator("llm_extra_body")
    @classmethod
    def validate_extra(cls, value: str) -> str:
        value = value or '{}'
        body = json.loads(value)
        if not isinstance(body, dict) or set(body) & {"model", "messages", "stream", "max_tokens"}:
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
