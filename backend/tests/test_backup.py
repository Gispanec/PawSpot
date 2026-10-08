import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from pawspot import backup


def test_docker_error_keeps_private_details_out_of_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, b"", b"synthetic private detail"
        ),
    )
    with pytest.raises(RuntimeError, match="pg_dump failed") as error:
        backup.docker(["exec", "test", "pg_dump"])
    assert "synthetic private detail" not in str(error.value)
    logs = list((tmp_path / ".local/backup-restore-errors").glob("*.log"))
    assert len(logs) == 1
    assert logs[0].read_bytes() == b"synthetic private detail"
    assert str(logs[0]) in str(error.value)


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def dump(
        arguments: list[str],
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
    ) -> bytes:
        assert stdout is not None
        stdout.write(b"PGDMPsynthetic-unit-test")
        return b""

    monkeypatch.setattr(backup, "docker", dump)
    photos = tmp_path / "media"
    photo = photos / str(uuid4()) / "main.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"synthetic-photo")
    return backup.create_backup(
        "test-container",
        "pawspot_test",
        "test_user",
        photos,
        tmp_path / "backups",
        writers_stopped=True,
    )


def test_backup_checksums_and_no_overwrite(snapshot: Path) -> None:
    hashes = backup.verify_backup(snapshot)
    assert len(hashes) == 2
    assert set(path.name for path in snapshot.iterdir()) == {
        "database.dump",
        "manifest.json",
        "photos",
    }


def test_backup_never_overwrites_an_existing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_time = MagicMock()
    fixed_time.now.return_value = datetime(2026, 10, 8, tzinfo=UTC)
    fixed_id = uuid4()
    monkeypatch.setattr(backup, "datetime", fixed_time)
    monkeypatch.setattr(backup, "uuid4", lambda: fixed_id)
    photos = tmp_path / "photos"
    photos.mkdir()
    destination = tmp_path / "backups"
    existing = destination / ("20261008T000000Z-" + fixed_id.hex[:8])
    existing.mkdir(parents=True)
    sentinel = existing / "keep"
    sentinel.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        backup.create_backup(
            "test", "pawspot", "pawspot", photos, destination, writers_stopped=True
        )
    assert sentinel.read_bytes() == b"keep"


def test_backup_rejects_destination_inside_photos(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside photo storage"):
        backup.create_backup(
            "test",
            "pawspot",
            "pawspot",
            tmp_path,
            tmp_path / "backups",
            writers_stopped=True,
        )


def test_backup_requires_stopped_writers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Stop Backend"):
        backup.create_backup(
            "test", "pawspot", "pawspot", tmp_path, tmp_path, writers_stopped=False
        )


def test_backup_rejects_secrets_in_photo_directory(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / ".env").write_text("synthetic-only")
    with pytest.raises(ValueError, match="Unexpected file"):
        backup.create_backup(
            "test",
            "pawspot",
            "pawspot",
            photos,
            tmp_path / "out",
            writers_stopped=True,
        )
    assert not (tmp_path / "out").exists()


def test_verify_detects_corruption(snapshot: Path) -> None:
    (snapshot / "database.dump").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        backup.verify_backup(snapshot)


def test_verify_rejects_path_traversal(snapshot: Path) -> None:
    manifest = json.loads((snapshot / "manifest.json").read_text())
    manifest["sha256"]["../.env"] = "0" * 64
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe backup path"):
        backup.verify_backup(snapshot)


@pytest.mark.parametrize("database", ["pawspot", "pawspot_test", "postgres", ""])
def test_restore_refuses_pilot_before_any_command(
    database: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="NEW pawspot_restore"):
        backup.restore_backup("test", "user", tmp_path, database, tmp_path)


def test_restore_preserves_existing_destination(snapshot: Path, tmp_path: Path) -> None:
    (tmp_path / "restore" / "pawspot_restore_test").mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        backup.restore_backup(
            "test", "user", snapshot, "pawspot_restore_test", tmp_path / "restore"
        )


def test_restore_uses_new_database_and_checks_photos(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def restore(
        arguments: list[str],
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
    ) -> bytes:
        calls.append(arguments)
        if stdin:
            assert stdin.read().startswith(b"PGDMP")
        return b""

    monkeypatch.setattr(backup, "docker", restore)
    photos = backup.restore_backup(
        "test", "user", snapshot, "pawspot_restore_test", tmp_path / "restore"
    )
    assert "createdb" in calls[0]
    assert "pg_restore" in calls[1]
    assert "--single-transaction" in calls[1]
    assert all("--clean" not in call for call in calls)
    assert next(photos.rglob("main.jpg")).read_bytes() == b"synthetic-photo"


def test_restore_stops_if_database_exists(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def existing(arguments: list[str], **kwargs: object) -> bytes:
        assert "createdb" in arguments
        raise RuntimeError("database already exists")

    monkeypatch.setattr(backup, "docker", existing)
    with pytest.raises(RuntimeError, match="already exists"):
        backup.restore_backup(
            "test", "user", snapshot, "pawspot_restore_test", tmp_path / "restore"
        )
    assert not (tmp_path / "restore").exists()
