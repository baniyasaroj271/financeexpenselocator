from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from typing import Any


class OCRUnavailableError(RuntimeError):
    """Raised when the Python OCR dependencies or Tesseract binary are unavailable."""


class OCRInputError(ValueError):
    """Raised when an uploaded receipt cannot be decoded safely."""


def _extract_money(value: str) -> float | None:
    cleaned = value.replace(",", "")
    match = re.search(r"(?<!\d)(\d+(?:\.\d{1,2})?)(?!\d)", cleaned)
    return round(float(match.group(1)), 2) if match else None


def _parse_date(text: str) -> str | None:
    patterns = (r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b")
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if len(match.group(1)) == 4:
                parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            else:
                year = int(match.group(3))
                parsed = date(2000 + year if year < 100 else year, int(match.group(2)), int(match.group(1)))
            return parsed.isoformat()
        except ValueError:
            continue
    return None


def _parse_receipt_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    upper = text.upper()
    vendor = lines[0][:120] if lines else ""
    total = subtotal = tax = None
    for label, target in (("TOTAL", "total"), ("GRAND TOTAL", "total"), ("SUBTOTAL", "subtotal"), ("SUB TOTAL", "subtotal"), ("TAX", "tax"), ("VAT", "tax")):
        for line in lines:
            if label in line.upper():
                amount = _extract_money(line.split(label, 1)[-1])
                if amount is not None:
                    if target == "total" and total is None:
                        total = amount
                    elif target == "subtotal" and subtotal is None:
                        subtotal = amount
                    elif target == "tax" and tax is None:
                        tax = amount
    items: list[dict[str, Any]] = []
    skip_words = ("total", "subtotal", "tax", "change", "cash", "date", "invoice", "receipt")
    for line in lines[1:]:
        if any(word in line.lower() for word in skip_words):
            continue
        amount = _extract_money(line)
        if amount is None or len(line) < 3:
            continue
        item_text = re.sub(r"(?<!\d)\d+(?:\.\d{1,2})?(?!\d)", "", line).strip(" .:-")
        if item_text:
            items.append({"name": item_text[:160], "quantity": 1, "price": amount})
    if total is None and lines:
        amounts = [_extract_money(line) for line in lines]
        total = max((amount for amount in amounts if amount is not None), default=None)
    warnings = []
    if not vendor:
        warnings.append("vendor")
    if not _parse_date(text):
        warnings.append("date")
    if subtotal is None:
        warnings.append("subtotal")
    if tax is None:
        warnings.append("tax")
    if total is None:
        warnings.append("total")
    return {
        "vendor": vendor,
        "date": _parse_date(text),
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "items": items[:50],
        "raw_text": text[:12000],
        "warnings": warnings,
    }


def extract_receipt(image_bytes: bytes) -> dict[str, Any]:
    try:
        from PIL import Image, UnidentifiedImageError
        import pytesseract
    except ImportError as exc:
        raise OCRUnavailableError("Install Pillow and pytesseract to enable receipt scanning.") from exc
    try:
        image = Image.open(BytesIO(image_bytes))
        image.verify()
        image = Image.open(BytesIO(image_bytes))
    except (UnidentifiedImageError, OSError) as exc:
        raise OCRInputError("The receipt image could not be decoded.") from exc
    try:
        text = pytesseract.image_to_string(image)
    except Exception as exc:
        raise OCRUnavailableError("Tesseract OCR is unavailable. Install Tesseract and configure its executable path.") from exc
    result = _parse_receipt_text(text)
    if not text.strip():
        result["warnings"] = ["No readable text was detected."]
    return result
