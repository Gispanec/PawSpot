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


@lru_cache
def get_settings() -> Settings:
    # BaseSettings читает обязательный пароль из окружения, что mypy не видит.
    return Settings()  # type: ignore[call-arg]
