import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class PhotoStorage(Protocol):
    def write(self, key: str, data: bytes) -> None: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class LocalPhotoStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if len(parts) != 2 or any(part in ("", ".", "..") for part in parts):
            raise ValueError("Invalid photo key")
        path = (self.root / parts[0] / parts[1]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid photo key")
        return path

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
