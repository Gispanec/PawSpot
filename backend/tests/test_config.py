import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from pawspot.config import CorsSettings, Settings, get_settings


def test_shared_dotenv_is_accepted_by_backend_and_alembic(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "PAWSPOT_DB_PASSWORD=test-password\n"
        "PAWSPOT_DB_NAME=pawspot_test\n"
        "PAWSPOT_CORS_ORIGINS=https://pawspot.example\n"
        "PAWSPOT_TELEGRAM_BOT_TOKEN=123456:test-token\n"
        "PAWSPOT_INTERNAL_SERVICE_TOKEN=test-service-token\n"
        "PAWSPOT_BOT_BACKEND_URL=http://127.0.0.1:8000\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PAWSPOT_DB_PASSWORD", raising=False)
    monkeypatch.delenv("PAWSPOT_DB_NAME", raising=False)
    monkeypatch.delenv("PAWSPOT_CORS_ORIGINS", raising=False)
    get_settings.cache_clear()
    try:
        settings = Settings()  # type: ignore[call-arg]
        assert settings.database_url.password == "test-password"
        assert settings.database_url.database == "pawspot_test"
        assert CorsSettings().allowed_origins == ["https://pawspot.example"]
        # Alembic env.py использует именно get_settings().database_url.
        assert get_settings().database_url == settings.database_url
    finally:
        get_settings.cache_clear()


def test_shared_dotenv_does_not_hide_invalid_backend_setting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAWSPOT_DB_PASSWORD=test-password\n"
        "PAWSPOT_DB_PORT=invalid\n"
        "PAWSPOT_BOT_BACKEND_URL=http://127.0.0.1:8000\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PAWSPOT_DB_PORT", raising=False)
    with pytest.raises(ValidationError, match="db_port"):
        Settings(_env_file=env_file)  # type: ignore[call-arg]


def test_alembic_offline_upgrade_accepts_shared_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "PAWSPOT_DB_PASSWORD=test-password\n"
        "PAWSPOT_BOT_BACKEND_URL=http://127.0.0.1:8000\n",
        encoding="utf-8",
    )
    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    project_root = alembic_ini.parents[1]
    test_ini = tmp_path / "alembic.ini"
    test_ini.write_text(
        alembic_ini.read_text(encoding="utf-8")
        .replace(
            "script_location = backend/migrations",
            f"script_location = {(project_root / 'backend/migrations').as_posix()}",
        )
        .replace(
            "prepend_sys_path = backend/src",
            f"prepend_sys_path = {(project_root / 'backend/src').as_posix()}",
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(test_ini),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout
