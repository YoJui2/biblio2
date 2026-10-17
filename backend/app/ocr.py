"""Lectura OCR de fichas escaneadas con TrOCR (Microsoft).

TrOCR reconoce **líneas** de texto manuscrito o impreso. Para leer una ficha
completa, este módulo segmenta la imagen en renglones (y columnas) y reconoce
cada fragmento; después interpreta el texto como una **propuesta editable**.

Nunca persiste datos: la interfaz muestra la imagen y los campos reconocidos
para que la persona los corrija antes de guardarlos.
"""

from __future__ import annotations

import io
import os
import re
import threading
import unicodedata

from app.inventory import parse_inventory_range

try:  # HEIC/HEIF de fotos de celular (dependencia opcional).
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIF_AVAILABLE = True
except Exception:  # noqa: BLE001 - si no está instalado, seguimos sin HEIC
    _HEIF_AVAILABLE = False

DEFAULT_MODEL = os.environ.get("TROCR_MODEL", "microsoft/trocr-base-handwritten")
MAX_IMAGE_BYTES = 20_000_000
MAX_SEGMENTS = 80
CONFIDENCE_THRESHOLD = 70.0
_BASE_FORMATS = "PNG, JPG, TIFF, BMP, WEBP o GIF"
SUPPORTED_FORMATS = _BASE_FORMATS + (", HEIC o AVIF" if _HEIF_AVAILABLE else "")

_MODEL_LOCK = threading.Lock()
_GENERATE_LOCK = threading.Lock()
_MODEL_STATE: dict[str, object] = {}

_INVENTORY_TOKEN = re.compile(
    r"(?:INV\s*[-.:#]?\s*)?\d+(?:\s*[-–—]\s*(?:INV\s*[-.:#]?\s*)?\d+)?",
    re.IGNORECASE,
)
_NUMBER_LIST_LINE = re.compile(
    r"^(?:\d{1,6}(?:\s*[-–—]\s*\d{1,6})?)"
    r"(?:[\s,;/]+(?:\d{1,6}(?:\s*[-–—]\s*\d{1,6})?))*$",
    re.IGNORECASE,
)
_NUMBER_TOKEN_SPLIT = re.compile(r"[\s,;/]+")
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
    """El modelo TrOCR no está disponible en el entorno."""


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
            if not _NUMBER_LIST_LINE.fullmatch(line):
                continue
            for token in _NUMBER_TOKEN_SPLIT.split(line):
                parsed = _try_parse_range(token)
                if parsed:
                    numbers.extend(parsed)
                    sources.append(token)
            if numbers:
                break
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
        if not _NUMBER_LIST_LINE.fullmatch(line)
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


def _get_model():
    """Carga (una sola vez) TrOCR. Devuelve (processor, model)."""

    if _MODEL_STATE.get("ready"):
        return _MODEL_STATE["processor"], _MODEL_STATE["model"]
    if _MODEL_STATE.get("error"):
        raise OcrUnavailable(str(_MODEL_STATE["error"]))

    with _MODEL_LOCK:
        if _MODEL_STATE.get("ready"):
            return _MODEL_STATE["processor"], _MODEL_STATE["model"]
        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError as error:
            _MODEL_STATE["error"] = (
                "Faltan las librerías torch/transformers en el servidor. "
                "Recompilá la API: docker compose up --build."
            )
            raise OcrUnavailable(str(_MODEL_STATE["error"])) from error
        try:
            processor = TrOCRProcessor.from_pretrained(DEFAULT_MODEL)
            model = VisionEncoderDecoderModel.from_pretrained(DEFAULT_MODEL)
            torch.set_num_threads(max(1, os.cpu_count() or 2))
            model.eval()
        except Exception as error:  # noqa: BLE001 - fallo de descarga/carga del modelo
            _MODEL_STATE["error"] = f"No se pudo cargar el modelo {DEFAULT_MODEL}: {error}"
            raise OcrUnavailable(str(_MODEL_STATE["error"])) from error
        _MODEL_STATE.update(ready=True, processor=processor, model=model)
        return processor, model


def _prepare_gray(data: bytes):
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as error:
        raise OcrUnavailable("Falta la librería Pillow en el servidor.") from error

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
    if gray.width > 3000:
        gray = gray.resize((3000, max(1, int(gray.height * 3000 / gray.width))))
    elif gray.width < 1600:
        gray = gray.resize((1600, max(1, int(gray.height * 1600 / gray.width))))
    return gray


def _segment(gray_image) -> list:
    """Divide la imagen en renglones/celdas de texto (crops de Pillow)."""

    import cv2
    import numpy as np

    gray = np.array(gray_image)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    height, width = binary.shape

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, width // 15), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, height // 15)))
    rules = cv2.add(
        cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel),
        cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel),
    )
    cleaned = cv2.subtract(binary, rules)

    row_sum = (cleaned > 0).sum(axis=1)
    row_threshold = max(2, int(width * 0.005))
    bands: list[tuple[int, int]] = []
    in_band = False
    start = 0
    for y, value in enumerate(row_sum):
        if value > row_threshold and not in_band:
            in_band, start = True, y
        elif value <= row_threshold and in_band:
            in_band = False
            if y - start >= 10:
                bands.append((start, y))
    if in_band:
        bands.append((start, height))

    crops = []
    for y0, y1 in bands:
        band = cleaned[y0:y1, :]
        col_sum = (band > 0).sum(axis=0)
        col_threshold = max(1, int((y1 - y0) * 0.04))
        gap_limit = max(20, width // 40)
        segments: list[tuple[int, int]] = []
        in_segment = False
        seg_start = 0
        gap = 0
        for x, value in enumerate(col_sum):
            if value > col_threshold:
                if not in_segment:
                    in_segment, seg_start = True, x
                gap = 0
            elif in_segment:
                gap += 1
                if gap > gap_limit:
                    in_segment = False
                    segments.append((seg_start, x - gap))
        if in_segment:
            segments.append((seg_start, width))
        pad_y = max(4, (y1 - y0) // 5)
        for x0, x1 in segments:
            if x1 - x0 < 8:
                continue
            crops.append(
                gray_image.crop(
                    (max(0, x0 - 6), max(0, y0 - pad_y), min(width, x1 + 6), min(height, y1 + pad_y))
                )
            )
    return crops


def _best_orientation(gray_image) -> tuple[list, object]:
    """Prueba 0/90/180/270 grados y devuelve la segmentación más plausible."""

    best_crops: list = []
    best_image = gray_image
    best_score = -1
    for angle in (0, 90, 180, 270):
        rotated = gray_image if angle == 0 else gray_image.rotate(angle, expand=True)
        try:
            crops = _segment(rotated)
        except Exception:  # noqa: BLE001 - OpenCV puede fallar; probamos otra orientación
            crops = []
        valid = [crop for crop in crops if crop.width > crop.height]
        if len(valid) > best_score:
            best_score = len(valid)
            best_crops = valid
            best_image = rotated
    return best_crops, best_image


def _recognize(processor, model, image) -> tuple[str, float]:
    import torch

    pixel_values = processor(images=image, return_tensors="pt").pixel_values
    with _GENERATE_LOCK, torch.no_grad():
        outputs = model.generate(
            pixel_values,
            max_length=64,
            output_scores=True,
            return_dict_in_generate=True,
        )
    text = processor.batch_decode(outputs.sequences, skip_special_tokens=True)[0].strip()
    confidence = 0.0
    try:
        transition = model.compute_transition_scores(
            outputs.sequences, outputs.scores, normalize_logits=True
        )
        probs = torch.exp(transition[0])
        if probs.numel():
            confidence = float(probs.mean()) * 100.0
    except Exception:  # noqa: BLE001 - la confianza es informativa
        confidence = 60.0 if text else 0.0
    return text, confidence


def scan_image_bytes(
    data: bytes,
    known_sections: list[str] | None = None,
) -> dict[str, object]:
    """Lee una imagen escaneada y devuelve una propuesta de campos."""

    processor, model = _get_model()
    gray = _prepare_gray(data)

    try:
        crops, oriented = _best_orientation(gray)
    except Exception:  # noqa: BLE001 - si OpenCV falta, usamos la imagen completa
        crops, oriented = [], gray
    if not crops:
        crops = [gray]

    lines: list[str] = []
    confidences: list[float] = []
    for crop in crops[:MAX_SEGMENTS]:
        try:
            text, confidence = _recognize(processor, model, crop)
        except Exception:  # noqa: BLE001 - una línea ilegible no cancela el resto
            continue
        if text:
            lines.append(text)
            confidences.append(confidence)

    raw_text = "\n".join(lines)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    result = interpret_ocr_text(raw_text, known_sections)
    width, height = oriented.size

    low_confidence = confidence < CONFIDENCE_THRESHOLD or not raw_text.strip()
    warnings = list(result["warnings"])
    if not raw_text.strip():
        warnings.insert(0, "No se reconoció texto (¿imagen borrosa o poco iluminada?).")
    elif low_confidence:
        warnings.insert(0, "La lectura salió con poca confianza; revisá los campos antes de guardar.")

    return {
        **result,
        "status": "low_confidence" if low_confidence else "ok",
        "engine": "trocr",
        "confidence": round(confidence, 1),
        "raw_text": raw_text,
        "width": width,
        "height": height,
        "warnings": warnings,
    }


def ocr_status() -> dict[str, object]:
    available = True
    detail = None
    try:
        _get_model()
    except OcrUnavailable as error:
        available = False
        detail = str(error)
    return {
        "available": available,
        "engine": "trocr",
        "model": DEFAULT_MODEL,
        "formats": SUPPORTED_FORMATS,
        "detail": detail,
    }
