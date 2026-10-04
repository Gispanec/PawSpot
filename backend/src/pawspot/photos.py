import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = 25_000_000
ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class InvalidPhoto(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedPhoto:
    main: bytes
    thumbnail: bytes
    width: int
    height: int


def process_photo(
    data: bytes, declared_mime: str | None, max_bytes: int
) -> ProcessedPhoto:
    if not data or len(data) > max_bytes:
        raise InvalidPhoto("Photo size is invalid")
    if declared_mime not in ALLOWED_FORMATS.values():
        raise InvalidPhoto("Unsupported photo MIME type")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_mime = ALLOWED_FORMATS.get(image.format or "")
                if actual_mime != declared_mime or getattr(image, "is_animated", False):
                    raise InvalidPhoto("Unsupported photo format")
                if image.width * image.height > MAX_PIXELS:
                    raise InvalidPhoto("Photo dimensions are too large")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                if oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = oriented.convert("RGB")
                rgb.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                main = io.BytesIO()
                rgb.save(main, format="JPEG", quality=85, exif=b"")
                thumb = rgb.copy()
                thumb.thumbnail((400, 400), Image.Resampling.LANCZOS)
                thumbnail = io.BytesIO()
                thumb.save(thumbnail, format="JPEG", quality=80, exif=b"")
                return ProcessedPhoto(
                    main=main.getvalue(),
                    thumbnail=thumbnail.getvalue(),
                    width=rgb.width,
                    height=rgb.height,
                )
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise InvalidPhoto("Invalid photo") from exc
