"""Lectura OCR de fichas escaneadas.

Tesseract devuelve texto sin estructura; este módulo lo interpreta como una
**propuesta editable**. Nunca persiste datos: la interfaz muestra los campos
reconocidos para que la persona los corrija antes de guardarlos.

Las dependencias pesadas (Pillow, pytesseract) se importan de forma perezosa
para que el resto de la API siga funcionando aunque Tesseract no esté
instalado en el entorno.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import unicodedata

from app.inventory import parse_inventory_range

try:  # HEIC/HEIF de fotos de celular (dependencia opcional).
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIF_AVAILABLE = True
except Exception:  # noqa: BLE001 - si no está instalado, seguimos sin HEIC
    _HEIF_AVAILABLE = False

try:  # AVIF (parte de pillow-heif, distinta función de registro).
    from pillow_heif import register_avif_opener

    register_avif_opener()
except Exception:  # noqa: BLE001 - AVIF es opcional
    pass

DEFAULT_LANGUAGES = "spa+eng"
MAX_IMAGE_BYTES = 20_000_000
CONFIDENCE_THRESHOLD = 70.0
TESSERACT_CONFIG = "--oem 3 --psm 6"
_BASE_FORMATS = "PNG, JPG, TIFF, BMP, WEBP o GIF"
SUPPORTED_FORMATS = _BASE_FORMATS + (", HEIC o AVIF" if _HEIF_AVAILABLE else "")

_INVENTORY_TOKEN = re.compile(
    r"(?:INV\s*[-.:#]?\s*)?\d+(?:\s*[-–—]\s*(?:INV\s*[-.:#]?\s*)?\d+)?",
    re.IGNORECASE,
)
_ONLY_NUMBER_LINE = re.compile(
    r"^(?:INV\s*[-.:#]?\s*)?\d+(?:\s*[-–—]\s*(?:INV\s*[-.:#]?\s*)?\d+)?$",
    re.IGNORECASE,
)
_LABEL_LINE = re.compile(r"^\s*(?P<label>[^:\-–—]{1,40}?)\s*[:\-–—]\s*(?P<value>.+?)\s*$")
_YEAR = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_IMPRINT_HINT = re.compile(r"\beditorial\b|\bediciones\b|\bed\.\b|\bimprenta\b", re.IGNORECASE)
_BINDING_KEYWORDS = (
    "cartone",
    "cartoné",
    "rustica",
    "rústica",
    "pasta",
    "tapa",
    "encuadernado",
    "encuadernacion",
    "empastado",
    "anillado",
    "bolsillo",
)

_LABELS = (
    ("inventory", r"inv|inventario|ninv|numeros?deinventario"),
    ("section", r"seccion|area|coleccion"),
    ("title", r"titulo|obra"),
    ("author", r"autor(?:a|es)?"),
    ("imprint", r"piedeimprenta|imprenta|editorial|edicion|publicacion"),
    ("binding", r"encuadernacion|observaciones|notas"),
)


class OcrError(Exception):
    """La imagen o el texto no se pudieron procesar."""


class OcrUnavailable(OcrError):
    """Tesseract no está disponible en el entorno."""


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    without_accents = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", without_accents.lower())


def _classify_line(line: str) -> tuple[str | None, str | None]:
    match = _LABEL_LINE.match(line)
    if match is None:
        return None, None
    label = normalize_text(match.group("label"))
    value = match.group("value").strip()
    if not label or not value:
        return None, None
    for name, pattern in _LABELS:
        if re.fullmatch(pattern, label):
            return name, value
    return None, None


def _try_parse_range(value: str) -> list[int] | None:
    try:
        return parse_inventory_range(value)
    except ValueError:
        return None


def _looks_imprint(line: str) -> bool:
    return bool(_YEAR.search(line) or _IMPRINT_HINT.search(line) or " : " in line)


def _looks_binding(line: str) -> bool:
    normalized = normalize_text(line)
    return any(normalize_text(word) in normalized for word in _BINDING_KEYWORDS)


def interpret_ocr_text(
    text: str, known_sections: list[str] | None = None
) -> dict[str, object]:
    """Convierte texto OCR en una propuesta de campos del catálogo."""

    sections = [section.strip() for section in (known_sections or []) if section and section.strip()]
    lines = [line.strip() for line in re.split(r"[\r\n]+", text or "")]
    lines = [line for line in lines if line]

    labeled: dict[str, list[str]] = {}
    content_lines: list[str] = []
    for line in lines:
        label, value = _classify_line(line)
        if label is not None and value is not None:
            labeled.setdefault(label, []).append(value)
        else:
            content_lines.append(line)

    section = labeled.get("section", [""])[0]
    if not section and sections:
        best = ""
        for line in lines:
            normalized_line = normalize_text(line)
            for candidate in sections:
                normalized_candidate = normalize_text(candidate)
                if normalized_candidate and (
                    normalized_line == normalized_candidate
                    or normalized_candidate in normalized_line
                ) and len(candidate) > len(best):
                    best = candidate
        section = best

    numbers: list[int] = []
    sources: list[str] = []
    for value in labeled.get("inventory", []):
        parsed = _try_parse_range(value)
        if parsed:
            numbers.extend(parsed)
            sources.append(value)
    if not numbers:
        for line in content_lines:
            if _ONLY_NUMBER_LINE.fullmatch(line):
                parsed = _try_parse_range(line)
                if parsed:
                    numbers.extend(parsed)
                    sources.append(line)
    if not numbers:
        for line in lines:
            for match in _INVENTORY_TOKEN.finditer(line):
                token = match.group(0)
                if re.search(r"inv", token, re.IGNORECASE) or re.search(r"[-–—]", token):
                    parsed = _try_parse_range(token)
                    if parsed:
                        numbers.extend(parsed)
                        sources.append(token.strip())
    numbers = sorted(set(numbers))
    inventory_source = sources[0] if sources else ""

    imprint = labeled.get("imprint", [""])[0]
    binding = labeled.get("binding", [""])[0]

    candidates = [
        line
        for line in content_lines
        if not _ONLY_NUMBER_LINE.fullmatch(line)
        and (not section or normalize_text(line) != normalize_text(section))
    ]
    if not imprint:
        imprint = next((line for line in candidates if _looks_imprint(line)), "")
    if not binding:
        binding = next((line for line in candidates if _looks_binding(line)), "")
    if imprint:
        candidates = [line for line in candidates if line != imprint]
    if binding:
        candidates = [line for line in candidates if line != binding]

    title = labeled.get("title", [""])[0]
    author = labeled.get("author", [""])[0]

    if not title:
        for line in candidates:
            for separator in (" / ", " ; ", " | "):
                if separator in line:
                    left, right = line.split(separator, 1)
                    title = left.strip()
                    author = author or right.strip()
                    break
            if title:
                break
    if not title and candidates:
        title = max(candidates, key=lambda line: len(re.sub(r"\W", "", line)))
    if not author:
        for line in candidates:
            if normalize_text(line).startswith("por") and line != title:
                author = re.sub(r"(?i)^por\s+", "", line).strip()
                break
    if not author and title in candidates:
        index = candidates.index(title)
        remaining = [line for line in candidates[index + 1 :] if line != title]
        if remaining:
            author = remaining[0]

    warnings: list[str] = []
    if not numbers:
        warnings.append("No reconocí números de inventario; completalos a mano.")
    if not title:
        warnings.append("No reconocí un título claro.")
    if not author:
        warnings.append("No reconocí un autor claro.")

    return {
        "section": section,
        "title": title,
        "author": author,
        "imprint": imprint,
        "binding": binding,
        "inventory_numbers": numbers,
        "inventory_source": inventory_source,
        "warnings": warnings,
    }


def _configure_tesseract() -> None:
    import pytesseract

    command = os.environ.get("TESSERACT_CMD", "").strip()
    if command and os.path.exists(command):
        pytesseract.pytesseract.tesseract_cmd = command


def _load_image(data: bytes):
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as error:
        raise OcrUnavailable(
            "Falta la librería Pillow en el servidor para procesar imágenes."
        ) from error

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise OcrError(
            f"No pude abrir la imagen. Formatos admitidos: {SUPPORTED_FORMATS}."
        ) from error

    if getattr(image, "is_animated", False):
        image.seek(0)
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    if gray.width and gray.width < 1000:
        scale = 1000 / gray.width
        gray = gray.resize((1000, max(1, int(gray.height * scale))))
    return gray


def _image_to_text(image, languages: str) -> tuple[str, float]:
    try:
        import pytesseract
        from pytesseract import Output, TesseractError, TesseractNotFoundError
    except ImportError as error:
        raise OcrUnavailable(
            "Falta la librería pytesseract en el servidor para leer imágenes."
        ) from error

    _configure_tesseract()
    try:
        data = pytesseract.image_to_data(
            image,
            lang=languages,
            config=TESSERACT_CONFIG,
            output_type=Output.DICT,
        )
        text = pytesseract.image_to_string(
            image,
            lang=languages,
            config=TESSERACT_CONFIG,
        )
    except TesseractNotFoundError as error:
        raise OcrUnavailable(
            "Tesseract no está instalado o no se encontró su binario. "
            "Ejecutá la API con la imagen de Docker que incluye tesseract-ocr "
            "(docker compose up --build)."
        ) from error
    except TesseractError as error:
        raise OcrError(
            f"Tesseract no pudo leer la imagen con los idiomas '{languages}': {error}."
        ) from error

    confidences = [
        float(value)
        for value in data.get("conf", [])
        if str(value).strip() not in ("", "-1")
    ]
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return text, confidence


def scan_image_bytes(
    data: bytes,
    languages: str = DEFAULT_LANGUAGES,
    known_sections: list[str] | None = None,
) -> dict[str, object]:
    """Lee una imagen escaneada y devuelve una propuesta de campos."""

    image = _load_image(data)
    text, confidence = _image_to_text(image, languages)
    result = interpret_ocr_text(text, known_sections)
    width, height = image.size
    low_confidence = confidence < CONFIDENCE_THRESHOLD
    warnings = list(result["warnings"])
    if low_confidence:
        warnings.insert(
            0,
            "La lectura salió con poca confianza; revisá los campos antes de guardar.",
        )
    return {
        **result,
        "status": "low_confidence" if low_confidence else "ok",
        "confidence": round(confidence, 1),
        "languages": languages,
        "raw_text": text,
        "width": width,
        "height": height,
        "warnings": warnings,
    }


def tesseract_status() -> dict[str, object]:
    binary = os.environ.get("TESSERACT_CMD", "").strip() or shutil.which("tesseract")
    try:
        import pytesseract

        _configure_tesseract()
        version = str(pytesseract.get_tesseract_version())
        languages = sorted(pytesseract.get_languages(config=""))
        return {
            "available": True,
            "version": version,
            "languages": languages,
            "default_languages": DEFAULT_LANGUAGES,
            "formats": SUPPORTED_FORMATS,
            "binary": binary or None,
            "detail": None,
        }
    except Exception as error:  # noqa: BLE001 - se informa cualquier fallo de entorno
        return {
            "available": False,
            "version": None,
            "languages": [],
            "default_languages": DEFAULT_LANGUAGES,
            "formats": SUPPORTED_FORMATS,
            "binary": binary or None,
            "detail": str(error),
        }
