from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAWSPOT_", env_file=".env", extra="ignore"
    )

    telegram_bot_token: SecretStr
    internal_service_token: SecretStr
    bot_backend_url: str = "http://127.0.0.1:8000"
    max_photo_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
