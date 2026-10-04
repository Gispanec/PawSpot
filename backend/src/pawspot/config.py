from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAWSPOT_", env_file=".env")

    db_host: str = "127.0.0.1"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "pawspot"
    db_user: str = "pawspot"
    db_password: SecretStr
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    public_location_radius_m: int = Field(default=200, ge=50, le=2000)
    telegram_bot_token: SecretStr | None = None
    internal_service_token: SecretStr | None = None
    allowed_telegram_ids: str = ""
    cors_origins: str = ""
    auth_init_data_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    auth_session_hours: int = Field(default=12, ge=1, le=168)
    default_city_slug: str = "tbilisi"
    matching_radius_m: int = Field(default=1000, ge=100, le=10000)
    matching_lookback_days: int = Field(default=90, ge=1, le=365)
    matching_max_candidates: int = Field(default=5, ge=1, le=20)
    draft_ttl_hours: int = Field(default=24, ge=1, le=168)
    media_dir: str = "media"
    max_photo_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    photo_cleanup_grace_hours: int = Field(default=48, ge=1, le=720)

    @property
    def allowlist(self) -> frozenset[int]:
        return frozenset(
            int(item.strip())
            for item in self.allowed_telegram_ids.split(",")
            if item.strip()
        )

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"connect_timeout": str(self.db_connect_timeout_seconds)},
        )


class CorsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAWSPOT_", env_file=".env")

    cors_origins: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    # BaseSettings читает обязательный пароль из окружения, что mypy не видит.
    return Settings()  # type: ignore[call-arg]
