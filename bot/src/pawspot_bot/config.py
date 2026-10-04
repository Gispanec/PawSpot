from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAWSPOT_", env_file=".env", extra="ignore"
    )

    telegram_bot_token: SecretStr
    internal_service_token: SecretStr
    bot_backend_url: str = "http://127.0.0.1:8000"
    mini_app_url: str = ""
    max_photo_bytes: int = Field(default=10 * 1024 * 1024, ge=1)

    @field_validator("mini_app_url")
    @classmethod
    def require_https_mini_app(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ValueError("Mini App URL must use HTTPS")
        return value
