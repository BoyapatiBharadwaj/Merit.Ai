"""Validated image decoding shared by AI and OCR paths."""
import base64
import binascii
import io

from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def decode_image(value: str) -> Image.Image:
    raw = value.split(",", 1)[1] if "," in value else value
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image payload must be valid base64 data.") from error
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image must be 5 MB or smaller.")
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        if image.format not in ALLOWED_FORMATS:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only JPEG, PNG, and WEBP images are accepted.")
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image dimensions are too large.")
        return image.convert("RGB")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded image is invalid or corrupted.") from error