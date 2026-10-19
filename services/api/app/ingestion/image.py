"""Safe local image normalization and optional Tesseract OCR."""

import hashlib
import importlib.util
import io
import math
import shutil
import warnings
from dataclasses import dataclass
from pathlib import PurePosixPath

from PIL import Image, ImageOps

from app.errors import ApiError

ALLOWED_FORMATS = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg")}


@dataclass(frozen=True)
class NormalizedImage:
    content: bytes
    content_type: str
    extension: str
    width: int
    height: int
    sha256: str


def safe_image_filename(filename: str | None) -> str:
    candidate = PurePosixPath((filename or "").replace("\\", "/")).name
    candidate = "".join(char for char in candidate if char.isprintable() and ord(char) != 127)
    return candidate.strip(" .")[:200] or "uploaded-image"


def normalize_image(
    content: bytes, *, max_bytes: int, max_dimension: int, max_pixels: int
) -> NormalizedImage:
    if not content:
        raise ApiError(422, "EMPTY_IMAGE", "The image file is empty.")
    if len(content) > max_bytes:
        raise ApiError(413, "IMAGE_TOO_LARGE", f"The image exceeds the {max_bytes}-byte limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                image_format = probe.format
                width, height = probe.size
                if image_format not in ALLOWED_FORMATS:
                    raise ApiError(
                        415, "UNSUPPORTED_IMAGE", "Only PNG and JPEG images are supported."
                    )
                if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension:
                    raise ApiError(
                        413,
                        "IMAGE_DIMENSION_LIMIT_EXCEEDED",
                        f"Image width and height must each be at most {max_dimension} pixels.",
                    )
                if width * height > max_pixels:
                    raise ApiError(
                        413,
                        "IMAGE_PIXEL_LIMIT_EXCEEDED",
                        f"The image exceeds the {max_pixels}-pixel limit.",
                    )
                if getattr(probe, "n_frames", 1) != 1:
                    raise ApiError(415, "UNSUPPORTED_IMAGE", "Animated images are not supported.")
                probe.verify()
            with Image.open(io.BytesIO(content)) as source:
                source.load()
                image = ImageOps.exif_transpose(source)
                width, height = image.size
                if width > max_dimension or height > max_dimension or width * height > max_pixels:
                    raise ApiError(
                        413,
                        "IMAGE_DIMENSION_LIMIT_EXCEEDED",
                        "The oriented image exceeds the configured dimension limit.",
                    )
                image_format = source.format
                output = io.BytesIO()
                if image_format == "JPEG":
                    image.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
                else:
                    image.save(output, format="PNG", optimize=True)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            422, "INVALID_IMAGE", "The uploaded image is malformed or cannot be decoded."
        ) from exc
    normalized = output.getvalue()
    content_type, extension = ALLOWED_FORMATS[image_format]
    return NormalizedImage(
        content=normalized,
        content_type=content_type,
        extension=extension,
        width=width,
        height=height,
        sha256=hashlib.sha256(normalized).hexdigest(),
    )


def ocr_available() -> bool:
    return (
        importlib.util.find_spec("pytesseract") is not None
        and shutil.which("tesseract") is not None
    )


def recognize_text(image_bytes: bytes) -> tuple[str, str | None]:
    """Return (status, text); OCR is local and reports missing runtime support explicitly."""
    if not ocr_available():
        return "unavailable", None
    try:
        import pytesseract

        with Image.open(io.BytesIO(image_bytes)) as image:
            text = pytesseract.image_to_string(image).strip()
    except Exception:
        return "failed", None
    return ("available", text) if text else ("empty", "")


def recognize_region(image_bytes: bytes, geometry: dict[str, float]) -> tuple[str, str | None]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        left = max(0, min(width - 1, int(geometry["x"] * width)))
        top = max(0, min(height - 1, int(geometry["y"] * height)))
        right = max(
            left + 1,
            min(width, math.ceil((geometry["x"] + geometry["width"]) * width - 1e-9)),
        )
        bottom = max(
            top + 1,
            min(height, math.ceil((geometry["y"] + geometry["height"]) * height - 1e-9)),
        )
        output = io.BytesIO()
        image.crop((left, top, right, bottom)).save(output, format="PNG")
    return recognize_text(output.getvalue())
