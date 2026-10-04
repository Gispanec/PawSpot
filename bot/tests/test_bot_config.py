from pathlib import Path

from pytest import MonkeyPatch

from pawspot_bot.config import BotSettings


def test_bot_reads_shared_dotenv_with_backend_fields(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAWSPOT_DB_PASSWORD=test-password\n"
        "PAWSPOT_DB_NAME=pawspot_test\n"
        "PAWSPOT_TELEGRAM_BOT_TOKEN=123456:test-token\n"
        "PAWSPOT_INTERNAL_SERVICE_TOKEN=test-service-token\n"
        "PAWSPOT_BOT_BACKEND_URL=http://127.0.0.1:8000\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PAWSPOT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PAWSPOT_INTERNAL_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("PAWSPOT_BOT_BACKEND_URL", raising=False)
    settings = BotSettings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.bot_backend_url == "http://127.0.0.1:8000"
    assert settings.telegram_bot_token.get_secret_value() == "123456:test-token"
