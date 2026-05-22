"""Application settings, loaded from environment variables and .env file."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized settings. All env vars flow through here."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    secret_key: str = "change-me-in-production"

    # Database
    database_url: str
    database_url_sync: str  # LangGraph PostgresSaver needs sync psycopg3

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    #
    # Model identifiers use the dated form (``claude-<family>-4-5-<YYYYMMDD>``)
    # for two reasons: (1) it's the form Anthropic guarantees is
    # available, the unsuffixed alias points at whichever date is
    # current and can shift under you; (2) the eval harness pins the
    # judge model — if the judge moves quarter to quarter, scores
    # stop being comparable. Override per-deployment via .env when a
    # newer model lands.
    anthropic_api_key: str
    llm_default_model: str = "claude-sonnet-4-5-20250929"
    llm_heavy_model: str = "claude-opus-4-5-20251115"
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_judge_model: str = "claude-opus-4-5-20251115"

    # Embeddings (OpenAI)
    #
    # text-embedding-3-small is the cheap, 5x-less-expensive sibling of
    # `-large`. With Matryoshka truncation, both models work fine at
    # 1024 dimensions — `-small` was originally 1536-native, `-large`
    # 3072. 1024 is the storage/perf sweet spot for either.
    openai_api_key: str = ""
    embeddings_model: str = "text-embedding-3-small"
    embeddings_dimensions: int = 1024

    # Email
    #
    # ``resend_from_address`` MUST be on a domain verified in Resend
    # (DNS records published, status: verified). The ubunifutech.com
    # domain is the canonical sender for Mkopo deployments. To use a
    # different domain in your own deployment:
    #   1. Verify the domain on https://resend.com/domains
    #   2. Set RESEND_FROM_ADDRESS to a mailbox on it
    #   3. Configure the inbound webhook (DEPLOY.md §Email).
    resend_api_key: str = ""
    resend_from_address: str = "mkopo@ubunifutech.com"
    resend_from_name: str = "Mkopo"
    resend_webhook_secret: str = ""

    # Document storage
    # STORAGE_BACKEND=local writes to STORAGE_ROOT on disk.
    # STORAGE_BACKEND=s3 writes to S3_BUCKET (point S3_ENDPOINT_URL at MinIO
    # or LocalStack to exercise the S3 codepath without an AWS account).
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: str = "./var/storage"
    aws_region: str = "us-west-2"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket: str = "mkopo-documents-dev"
    s3_endpoint_url: str = ""

    # Eval harness
    eval_golden_set_dir: str = "./evals/golden_sets"
    eval_results_dir: str = "./evals/results"

    # Auth
    dev_api_token: str = "dev-token-replace-me"

    # Frontend
    frontend_url: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def database_url_libpq(self) -> str:
        """The DB URL in the plain libpq format that raw psycopg expects.

        SQLAlchemy DSNs carry a `+driver` suffix (e.g. `postgresql+psycopg://...`)
        that psycopg's own parser rejects. LangGraph's `AsyncPostgresSaver` uses
        raw psycopg, so it needs the bare `postgresql://...` form.
        """
        return self.database_url_sync.replace("postgresql+psycopg://", "postgresql://", 1).replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Import this everywhere config is needed."""
    return Settings()  # type: ignore[call-arg]
