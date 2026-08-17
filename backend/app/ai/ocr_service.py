"""ID-card OCR using EasyOCR (a deep-learning text detector/recognizer),
which is meaningfully more accurate than Tesseract on the low-quality,
phone-camera ID photos students typically submit.
"""
import re
import threading
from functools import lru_cache

import numpy as np
from fastapi import HTTPException, status

from app.ai.image_utils import decode_image

# Same defensive reasoning as the locks in face_service.py/pose_service.py.
_ocr_lock = threading.Lock()


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Z ]", "", text.upper()).strip()


@lru_cache
def _easyocr_reader():
    try:
        import easyocr
        return easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ID-card verification is unavailable because the OCR engine (EasyOCR) is not installed on this server.",
        ) from error


def extract_text(base64_image: str) -> str:
    # Reuse the shared, validated decoder (type/size/dimension checks) instead of a
    # bespoke decode path so OCR uploads get the same protections as the AI endpoints.
    image = decode_image(base64_image)
    reader = _easyocr_reader()
    try:
        with _ocr_lock:
            results = reader.readtext(np.asarray(image), detail=1, paragraph=False)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The ID-card image could not be read. Try a clearer photo.") from error
    # results is a list of (bounding_box, text, confidence); join the recognized
    # text fragments in reading order for the name-matching step below.
    return " ".join(text for _bbox, text, _confidence in results).strip()


def verify_id_card(base64_image: str, registered_name: str) -> dict:
    raw_text = extract_text(base64_image)
    name_tokens = [token for token in _normalize(registered_name).split() if len(token) > 1]
    if not name_tokens:
        return {"extracted_text": raw_text, "name_matched": False, "message": "Registered name is invalid."}
    matched_ratio = sum(1 for token in name_tokens if token in _normalize(raw_text)) / len(name_tokens)
    name_matched = matched_ratio >= 0.5
    return {"extracted_text": raw_text, "name_matched": name_matched, "message": "ID card name matches student profile." if name_matched else "ID card name does not sufficiently match the registered student name."}
