"""Application settings, loaded from environment variables and optional .env files."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    # Later files win, and real environment variables beat both.
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # Comma-separated. Defaults to APP_URL when empty.
    cors_origins: str = ""

    database_url: str = "postgresql+psycopg://contentpilot:contentpilot@localhost:5433/contentpilot"
    redis_url: str = "redis://localhost:6380/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # Signs access tokens. Refresh tokens are random values stored hashed in the database.
    jwt_secret: SecretStr | None = None
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    google_ai_api_key: SecretStr | None = None

    # AI routing (spec section 68): cheap model for simple tasks, stronger one for writing.
    ai_provider: Literal["gemini"] = "gemini"
    gemini_fast_model: str = "gemini-3.5-flash-lite"
    gemini_quality_model: str = "gemini-3.8-flash"
    # Used when the main model is overloaded or rate limited. Empty disables.
    gemini_fast_fallback_model: str = "gemini-3.1-flash-lite"
    gemini_quality_fallback_model: str = "gemini-3.5-flash"
    gemini_embedding_model: str = "gemini-embedding-2"

    # Encrypts social OAuth tokens at rest. Comma-separated Fernet keys: the first encrypts,
    # all decrypt, so keys can be rotated. Required outside development/test.
    token_encryption_key: SecretStr | None = None

    linkedin_client_id: str = ""
    linkedin_client_secret: SecretStr | None = None
    # Posts API version (YYYYMM). LinkedIn retires versions about a year after release.
    linkedin_api_version: str = "202609"

    # X app (console.x.com), OAuth 2.0 "Web App" client. Posting is billed per post.
    x_client_id: str = ""
    x_client_secret: SecretStr | None = None

    # Meta app (developers.facebook.com) for publishing to Facebook Pages.
    facebook_client_id: str = ""
    facebook_client_secret: SecretStr | None = None
    facebook_graph_version: str = "v25.0"
    # Apps using "Facebook Login for Business" choose permissions in a Configuration and
    # send its ID instead of a scope list. Leave empty for classic Facebook Login.
    facebook_login_config_id: str = ""

    # Where generated media is stored. "local" writes to MEDIA_ROOT and the API serves it at /media.
    storage_backend: Literal["local"] = "local"
    media_root: str = str(BACKEND_DIR / "media")
    # Public base URL for media; defaults to API_URL + /media.
    media_base_url: str = ""

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Managed Postgres providers hand out postgres:// or postgresql:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @model_validator(mode="after")
    def _default_celery_urls(self) -> "Settings":
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        return self

    @model_validator(mode="after")
    def _require_strong_secret_outside_dev(self) -> "Settings":
        if self.app_env in ("staging", "production"):
            secret = self.jwt_secret.get_secret_value() if self.jwt_secret else ""
            if len(secret) < 32:
                raise ValueError("JWT_SECRET must be set to at least 32 characters in staging/production.")
            if not self.token_encryption_key:
                raise ValueError("TOKEN_ENCRYPTION_KEY must be set in staging/production.")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def media_url(self) -> str:
        return (self.media_base_url or f"{self.api_url.rstrip('/')}/media").rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        # Plain-http localhost in development/test can't use Secure cookies.
        return self.app_env in ("staging", "production")

    @property
    def allowed_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins or [self.app_url]


@lru_cache
def get_settings() -> Settings:
    return Settings()
