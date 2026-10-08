"""Операторский backup/restore локального Docker PostgreSQL и photo storage."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


def docker(
    arguments: list[str],
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> bytes:
    result = subprocess.run(
        ["docker", *arguments],
        stdin=stdin,
        stdout=stdout or subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        # Сохраняем причину локально: stderr может содержать данные БД.
        diagnostics = Path(".local/backup-restore-errors")
        diagnostics.mkdir(parents=True, exist_ok=True)
        log = diagnostics / f"{uuid4().hex}.log"
        with log.open("xb") as output:
            output.write(result.stderr)
        command = next(
            (arg for arg in arguments if arg in {"pg_dump", "pg_restore", "createdb"}),
            "exec",
        )
        raise RuntimeError(
            f"Docker {command} failed (exit {result.returncode}); "
            f"backup/restore incomplete. Private diagnostic: {log.resolve()}"
        )
    return result.stdout or b""


def identifier(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise ValueError("Database/user must be a simple PostgreSQL identifier")
    return value


def photo_key(value: str) -> bool:
    return (
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/(?:main|thumbnail)\.jpg",
            value,
        )
        is not None
    )


def safe_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError("Photo directory must exist and must not be a link")
    files = []
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Links are forbidden in photo storage/backup")
        if path.is_file():
            files.append(path)
    return files


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def create_backup(
    container: str,
    database: str,
    user: str,
    photos: Path,
    destination: Path,
    *,
    writers_stopped: bool,
) -> Path:
    if not writers_stopped:
        raise ValueError("Stop Backend, Bot and other writers; pass --writers-stopped")
    identifier(database)
    identifier(user)
    if destination.resolve().is_relative_to(photos.resolve()):
        raise ValueError("Backup destination must be outside photo storage")
    files = safe_files(photos)
    for path in files:
        if not photo_key(path.relative_to(photos).as_posix()):
            raise ValueError("Unexpected file in photo storage; backup aborted")
    # Отдельный каталог каждого snapshot; даже одинаковая дата не перезапишет копию.
    snapshot = destination / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    snapshot.mkdir(parents=True, exist_ok=False)
    with (snapshot / "database.dump").open("xb") as output:
        docker(
            [
                "exec",
                container,
                "pg_dump",
                "-U",
                user,
                "-d",
                database,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--no-password",
            ],
            stdout=output,
        )
    (snapshot / "photos").mkdir()
    for path in files:
        target = snapshot / "photos" / path.relative_to(photos)
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, target)
    hashes = {"database.dump": digest(snapshot / "database.dump")}
    hashes.update(
        {
            "photos/" + path.relative_to(photos).as_posix(): digest(
                snapshot / "photos" / path.relative_to(photos)
            )
            for path in files
        }
    )
    manifest = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_database": database,
        "sha256": hashes,
    }
    with (snapshot / "manifest.json").open("x", encoding="utf-8") as output_manifest:
        json.dump(manifest, output_manifest, indent=2)
    verify_backup(snapshot)
    return snapshot


def verify_backup(snapshot: Path) -> dict[str, str]:
    files = safe_files(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Unsupported backup manifest")
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict) or "database.dump" not in hashes:
        raise ValueError("Incomplete backup manifest")
    for key, value in hashes.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("Invalid backup manifest")
        if key != "database.dump" and not (
            key.startswith("photos/") and photo_key(key[7:])
        ):
            raise ValueError("Unsafe backup path")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid SHA-256")
    actual = {path.relative_to(snapshot).as_posix() for path in files}
    if actual != set(hashes) | {"manifest.json"}:
        raise ValueError("Backup files differ from manifest")
    for key, value in hashes.items():
        if digest(snapshot / key) != value:
            raise ValueError(f"Checksum mismatch: {key}")
    with (snapshot / "database.dump").open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise ValueError("Not a PostgreSQL custom-format dump")
    return hashes


def restore_backup(
    container: str, user: str, snapshot: Path, database: str, restore_root: Path
) -> Path:
    if re.fullmatch(r"pawspot_restore_[a-z0-9_]+", database) is None:
        raise ValueError("Restore requires a NEW pawspot_restore_<suffix> database")
    identifier(database)
    identifier(user)
    hashes = verify_backup(snapshot)
    photos = restore_root.resolve() / database / "media"
    if photos.parent.exists():
        raise ValueError("Restore destination already exists; choose another suffix")
    # createdb откажет для существующей БД; никогда не используем DROP/--clean.
    docker(
        [
            "exec",
            container,
            "createdb",
            "-U",
            user,
            "--no-password",
            "--template=template0",
            database,
        ]
    )
    with (snapshot / "database.dump").open("rb") as source:
        docker(
            [
                "exec",
                "-i",
                container,
                "pg_restore",
                "-U",
                user,
                "-d",
                database,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
                "--no-password",
            ],
            stdin=source,
        )
    photos.mkdir(parents=True, exist_ok=False)
    for key, value in hashes.items():
        if key == "database.dump":
            continue
        target = photos / key[7:]
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(snapshot / key, target)
        if digest(target) != value:
            raise ValueError("Restored photo checksum mismatch")
    return photos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "verify", "restore"])
    parser.add_argument("--container", default="infra-postgres-1")
    parser.add_argument("--database", default="pawspot")
    parser.add_argument("--user", default="pawspot")
    parser.add_argument("--photos", type=Path, default=Path("media"))
    parser.add_argument("--destination", type=Path, default=Path("backups"))
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "backup":
            result = create_backup(
                args.container,
                args.database,
                args.user,
                args.photos,
                args.destination,
                writers_stopped=args.writers_stopped,
            )
            print(f"Verified backup: {result}")
        else:
            if args.snapshot is None:
                raise ValueError("--snapshot is required")
            if args.operation == "verify":
                hashes = verify_backup(args.snapshot)
                print(f"Verified checksums: database + {len(hashes) - 1} photos")
            else:
                result = restore_backup(
                    args.container,
                    args.user,
                    args.snapshot,
                    args.database,
                    Path(".local/restore"),
                )
                print(f"Restored NEW database {args.database}; photos: {result}")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Backup/restore failed: {error}\n")


if __name__ == "__main__":
    main()
