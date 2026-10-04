import io
from pathlib import Path

import pytest
from PIL import Image

from pawspot.photos import InvalidPhoto, process_photo
from pawspot.storage import LocalPhotoStorage


def make_photo(*, exif: bool = False) -> bytes:
    image = Image.new("RGB", (1200, 800), "red")
    output = io.BytesIO()
    metadata = Image.Exif()
    if exif:
        metadata[270] = "private note"
        metadata[274] = 6
    image.save(output, format="JPEG", exif=metadata)
    return output.getvalue()


def test_processing_strips_exif_and_creates_thumbnail() -> None:
    processed = process_photo(make_photo(exif=True), "image/jpeg", 10 * 1024 * 1024)
    with Image.open(io.BytesIO(processed.main)) as main:
        assert main.format == "JPEG"
        assert main.getexif() == {}
        assert main.size == (800, 1200)
    with Image.open(io.BytesIO(processed.thumbnail)) as thumbnail:
        assert thumbnail.getexif() == {}
        assert max(thumbnail.size) <= 400


@pytest.mark.parametrize("case", ["invalid", "wrong_mime", "oversized"])
def test_invalid_or_oversized_photo_rejected(case: str) -> None:
    data = b"not an image" if case == "invalid" else make_photo()
    mime = "image/png" if case == "wrong_mime" else "image/jpeg"
    limit = 100 if case == "oversized" else 100000
    with pytest.raises(InvalidPhoto):
        process_photo(data, mime, limit)


def test_local_storage_uses_safe_keys(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(str(tmp_path))
    storage.write("photo/main.jpg", b"safe")
    assert storage.read("photo/main.jpg") == b"safe"
    storage.delete("photo/main.jpg")
    with pytest.raises(ValueError):
        storage.write("../outside.jpg", b"unsafe")
