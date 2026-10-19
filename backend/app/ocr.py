"""Lectura OCR de fichas escaneadas.

Motor principal: la API de visión de DeepSeek (formato OpenAI-compatible), que
transcribe la ficha completa. Respaldo local: TrOCR (Microsoft), que reconoce
**líneas** de texto manuscrito o impreso y requiere segmentar la imagen.

En ambos casos el texto reconocido se interpreta como una **propuesta editable**.
Nunca persiste datos: la interfaz muestra la imagen y los campos reconocidos
para que la persona los corrija antes de guardarlos.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import threading
import unicodedata
from pathlib import Path

from app.inventory import parse_inventory_range

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:  # Permite leer la API key desde .env al correr la API fuera de Docker.
    from dotenv import dotenv_values

    _DOTENV_VALUES = dotenv_values(_PROJECT_ROOT / ".env")
except Exception:  # noqa: BLE001 - sin python-dotenv usamos solo el entorno
    _DOTENV_VALUES = {}


def _setting(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if not value:
        value = _DOTENV_VALUES.get(name)
    if not value:
        return default
    return str(value).strip()

try:  # HEIC/HEIF de fotos de celular (dependencia opcional).
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIF_AVAILABLE = True
except Exception:  # noqa: BLE001 - si no está instalado, seguimos sin HEIC
    _HEIF_AVAILABLE = False

DEFAULT_MODEL = os.environ.get("TROCR_MODEL", "microsoft/trocr-base-handwritten")
DEFAULT_DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MAX_IMAGE_BYTES = 20_000_000
MAX_SEGMENTS = 80
PROBE_SEGMENTS = 6
CONFIDENCE_THRESHOLD = 70.0
_BASE_FORMATS = "PNG, JPG, TIFF, BMP, WEBP o GIF"
SUPPORTED_FORMATS = _BASE_FORMATS + (", HEIC o AVIF" if _HEIF_AVAILABLE else "")

NOT_INVENTORY_MARKER = "NO_ES_INVENTARIO"
_DEEPSEEK_SYSTEM_PROMPT = (
    "Sos un transcriptor experto de fichas de inventario de biblioteca escritas a mano. "
    "Transcribí con fidelidad todo el texto visible de la imagen, respetando mayúsculas, "
    "acentos y números de inventario, sin inventar ni resumir nada. "
    "Si la imagen NO es una planilla o ficha de inventario de biblioteca (por ejemplo, "
    "una foto personal, un paisaje, un comprobante o una captura de pantalla), respondé "
    f"exactamente {NOT_INVENTORY_MARKER} y nada más."
)
_DEEPSEEK_USER_PROMPT = (
    "Analizá la imagen. Si NO es una planilla o ficha de inventario de biblioteca, "
    f"respondé únicamente {NOT_INVENTORY_MARKER}.\n"
    "Si SÍ lo es, la imagen tiene una fila por libro: transcribí TODAS las filas, sin "
    "omitir ninguna y sin inventar datos.\n"
    "Devolvé una línea por libro, sin encabezados, sin numeración de fila y sin bloques "
    "de código, con los campos en este orden separados por \" | \":\n"
    "Nº de inventario | Autor | Título | Encuadernación | Pie de imprenta\n"
    "Reglas:\n"
    "- En el primer campo poné TODOS los números de inventario de esa fila, separados por comas.\n"
    "- Respetá mayúsculas, acentos y números; no resumas ni traduzcas.\n"
    "- Si un dato no aparece, dejá el campo vacío pero conservá los separadores \" | \".\n"
    "- No agregues comentarios, aclaraciones ni líneas de resumen."
)

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
    ("title", r"titulo|obra"),
    ("author", r"autor(?:a|es)?"),
    ("imprint", r"pie(?:de)?imprenta|imprenta|editorial|edicion|publicacion|pie"),
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


def _parse_inventory_value(value: str) -> tuple[list[int], list[str]]:
    """Extrae todos los números de un valor OCR, sea un rango o una lista."""

    parsed = _try_parse_range(value)
    if parsed is not None:
        return parsed, [value.strip()]

    numbers: list[int] = []
    sources: list[str] = []
    for match in _INVENTORY_TOKEN.finditer(value):
        token = match.group(0).strip()
        parsed = _try_parse_range(token)
        if parsed:
            numbers.extend(parsed)
            sources.append(token)
    return numbers, sources


def _compact_inventory_numbers(numbers: list[int]) -> str:
    """Representa una lista de números como rangos compactos (62-64, 70)."""

    values = sorted(set(numbers))
    if not values:
        return ""
    parts: list[str] = []
    start = previous = values[0]
    for number in values[1:]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(parts)


def _looks_imprint(line: str) -> bool:
    return bool(_YEAR.search(line) or _IMPRINT_HINT.search(line) or " : " in line)


def _looks_binding(line: str) -> bool:
    normalized = normalize_text(line)
    return any(normalize_text(word) in normalized for word in _BINDING_KEYWORDS)


def interpret_ocr_text(text: str) -> dict[str, object]:
    """Convierte texto OCR en una propuesta de campos del catálogo."""

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

    numbers: list[int] = []
    sources: list[str] = []
    for value in labeled.get("inventory", []):
        parsed, value_sources = _parse_inventory_value(value)
        if parsed:
            numbers.extend(parsed)
            sources.extend(value_sources)
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
    if len(sources) == 1:
        inventory_source = sources[0]
    elif sources:
        inventory_source = _compact_inventory_numbers(numbers)
    else:
        inventory_source = ""

    imprint = labeled.get("imprint", [""])[0]
    binding = labeled.get("binding", [""])[0]

    candidates = [
        line for line in content_lines if not _NUMBER_LIST_LINE.fullmatch(line)
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
        "title": title,
        "author": author,
        "imprint": imprint,
        "binding": binding,
        "inventory_numbers": numbers,
        "inventory_source": inventory_source,
        "warnings": warnings,
    }


_RECORD_LINE_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _split_record_rows(text: str) -> list[list[str]]:
    """Divide el texto del modelo en filas de campos separadas por '|'."""

    rows: list[list[str]] = []
    for raw_line in re.split(r"[\r\n]+", text or ""):
        line = _RECORD_LINE_PREFIX.sub("", raw_line.strip()).strip()
        line = line.strip("`").strip()
        if not line or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if all(not part for part in parts):
            continue
        while len(parts) < 5:
            parts.append("")
        rows.append(parts[:5])
    return rows


def interpret_ocr_records_text(text: str) -> list[dict[str, object]]:
    """Convierte el texto OCR de una planilla en varias propuestas de registro.

    Espera una línea por libro con los campos separados por '|' en el orden
    inventario, autor, título, encuadernación y pie de imprenta. Si no encuentra
    filas con ese formato, cae al intérprete de un solo registro.
    """

    rows = _split_record_rows(text)
    if not rows:
        return [interpret_ocr_text(text)]

    records: list[dict[str, object]] = []
    for inventory_text, author, title, binding, imprint in rows:
        numbers, _ = _parse_inventory_value(inventory_text)
        numbers = sorted(set(numbers))
        warnings: list[str] = []
        if not numbers:
            warnings.append("Sin números de inventario.")
        if not title:
            warnings.append("Sin título.")
        if not author:
            warnings.append("Sin autor.")
        records.append(
            {
                "title": title,
                "author": author,
                "imprint": imprint,
                "binding": binding,
                "inventory_numbers": numbers,
                "inventory_source": _compact_inventory_numbers(numbers),
                "warnings": warnings,
            }
        )
    return records


def _plausible_inventory_number(number: object) -> bool:
    try:
        value = int(number)
    except (TypeError, ValueError):
        return False
    return 0 < value <= 9_999_999


def looks_like_inventory_records(records: list[dict[str, object]]) -> bool:
    """True si al menos una fila parece una ficha real del inventario.

    Exigimos un número de inventario válido y contenido bibliográfico (título o
    autor). Así se rechazan fotos, comprobantes y capturas que no corresponden
    al formato de la planilla.
    """

    for record in records or []:
        numbers = [
            number
            for number in (record.get("inventory_numbers") or [])
            if _plausible_inventory_number(number)
        ]
        title = str(record.get("title") or "").strip()
        author = str(record.get("author") or "").strip()
        if numbers and (title or author):
            return True
    return False


def _reject_not_inventory() -> None:
    raise OcrError(
        "La imagen no parece una planilla o ficha de inventario de biblioteca. "
        "Subí una foto o escaneo donde se vean los números de inventario, el autor "
        "y el título de los libros."
    )


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

    # Descartamos manchas y estructuras grandes (espiral de encuadernación,
    # sombras del fondo, bordes). Si no, su altura fusiona todos los renglones
    # en una sola banda y TrOCR termina leyendo columnas verticales sin texto.
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    max_component_height = max(30, int(height * 0.7))
    max_component_width = max(30, int(width * 0.92))
    for index in range(1, component_count):
        _, _, component_w, component_h, _ = stats[index]
        if component_h > max_component_height or component_w > max_component_width:
            cleaned[labels == index] = 0

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


def _candidate_orientations(gray_image) -> list[tuple[object, list]]:
    """Devuelve orientaciones a probar, con su segmentación en renglones.

    El orden es solo un desempate: la orientación ganadora se elige luego según
    la confianza real del reconocimiento, no por una heurística geométrica.
    """

    candidates: list[tuple[object, list]] = []
    for angle in (0, 90, 270, 180):
        rotated = gray_image if angle == 0 else gray_image.rotate(angle, expand=True)
        try:
            crops = _segment(rotated)
        except Exception as error:  # noqa: BLE001 - OpenCV puede faltar; probamos otra
            logger.info("Segmentación falló a %s°: %s", angle, error)
            crops = []
        crops = [crop for crop in crops if crop.width > crop.height] or crops
        candidates.append((rotated, crops))
    return candidates


def _read_crops(
    processor, model, crops
) -> tuple[list[str], list[float], str | None]:
    lines: list[str] = []
    confidences: list[float] = []
    first_error: str | None = None
    for crop in crops:
        try:
            text, confidence = _recognize(processor, model, crop)
        except Exception as error:  # noqa: BLE001 - una línea ilegible no cancela el resto
            logger.exception("TrOCR no pudo leer un segmento")
            if first_error is None:
                first_error = f"{type(error).__name__}: {error}"
            continue
        if text:
            lines.append(text)
            confidences.append(confidence)
    return lines, confidences, first_error


def _recognize(processor, model, image) -> tuple[str, float]:
    import torch

    if image.mode != "RGB":
        image = image.convert("RGB")
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


def _deepseek_api_key() -> str:
    return _setting("DEEPSEEK_API_KEY")


def _deepseek_base_url() -> str:
    base = _setting("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
    return base.rstrip("/")


def _deepseek_model() -> str:
    return _setting("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)


def selected_engine() -> str:
    """Motor OCR activo: 'deepseek' o 'trocr'.

    Se puede forzar con la variable OCR_ENGINE. Si no se define, se usa DeepSeek
    cuando hay una API key configurada y TrOCR como respaldo.
    """

    engine = _setting("OCR_ENGINE").lower()
    if engine in ("deepseek", "trocr"):
        return engine
    return "deepseek" if _deepseek_api_key() else "trocr"


def _encode_image_for_api(data: bytes) -> tuple[str, int, int]:
    """Reencoda la imagen a JPEG y la devuelve en base64 junto a su tamaño."""

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
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return encoded, width, height


def _clean_model_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _scan_with_deepseek(data: bytes) -> dict[str, object]:
    """Transcribe la ficha con un modelo de visión de DeepSeek (API OpenAI-compatible)."""

    api_key = _deepseek_api_key()
    if not api_key:
        raise OcrUnavailable(
            "Falta la clave de DeepSeek. Configurá DEEPSEEK_API_KEY en el archivo .env."
        )
    model = _deepseek_model()
    base_url = _deepseek_base_url()
    encoded, width, height = _encode_image_for_api(data)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _DEEPSEEK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _DEEPSEEK_USER_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "temperature": 0,
        "stream": False,
    }

    try:
        import httpx

        timeout = float(_setting("DEEPSEEK_TIMEOUT", "90"))
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except ImportError as error:
        raise OcrUnavailable("Falta la librería httpx en el servidor.") from error
    except Exception as error:  # noqa: BLE001 - red/DNS/timeout
        raise OcrError(f"No pude contactar la API de DeepSeek: {error}") from error

    if response.status_code >= 400:
        detail = response.text.strip()[:500]
        raise OcrError(
            f"La API de DeepSeek respondió con el estado {response.status_code}: {detail}"
        )

    try:
        body = response.json()
        raw_text = _clean_model_text(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise OcrError("La respuesta de DeepSeek no tuvo el formato esperado.") from error

    records = interpret_ocr_records_text(raw_text)
    has_structured_rows = bool(_split_record_rows(raw_text))
    if (
        NOT_INVENTORY_MARKER in raw_text.upper()
        or not has_structured_rows
        or not looks_like_inventory_records(records)
    ):
        logger.info("OCR DeepSeek: se rechazó la imagen por no parecer del formato de inventario.")
        _reject_not_inventory()

    confidence = 100.0 if raw_text else 0.0
    logger.info(
        "OCR DeepSeek (%s): %d línea(s), %d registro(s) reconocido(s).",
        model,
        len(raw_text.splitlines()),
        len(records),
    )

    low_confidence = not raw_text
    warnings: list[str] = []
    if low_confidence:
        warnings.insert(0, "El modelo de DeepSeek no devolvió texto para esta imagen.")
    if not records:
        warnings.append("No reconocí ninguna fila con el formato esperado.")

    return {
        "status": "low_confidence" if low_confidence else "ok",
        "engine": "deepseek",
        "confidence": round(confidence, 1),
        "raw_text": raw_text,
        "width": width,
        "height": height,
        "records": records,
        "warnings": warnings,
    }


def scan_image_bytes(data: bytes) -> dict[str, object]:
    """Lee una imagen escaneada y devuelve una propuesta de campos."""

    if selected_engine() == "deepseek":
        return _scan_with_deepseek(data)
    return _scan_with_trocr(data)


def _scan_with_trocr(data: bytes) -> dict[str, object]:
    """Lee una imagen escaneada con TrOCR y devuelve una propuesta de campos."""

    processor, model = _get_model()
    gray = _prepare_gray(data)

    try:
        candidates = _candidate_orientations(gray)
    except Exception as error:  # noqa: BLE001 - si OpenCV falta, usamos la imagen completa
        logger.info("No se pudo segmentar la imagen: %s", error)
        candidates = [(gray, [gray])]
    if not candidates:
        candidates = [(gray, [gray])]

    best_score = -1.0
    best_pool: list = []
    best_oriented = gray
    best_lines: list[str] = []
    best_confidences: list[float] = []
    best_error: str | None = None
    for rotated, crops in candidates:
        pool = crops or [rotated]
        lines, confidences, error = _read_crops(processor, model, pool[:PROBE_SEGMENTS])
        score = sum(confidences)
        if score > best_score or (score == best_score and len(lines) > len(best_lines)):
            best_score = score
            best_oriented = rotated
            best_pool = pool
            best_lines = lines
            best_confidences = confidences
            best_error = error

    if len(best_pool) > PROBE_SEGMENTS:
        lines, confidences, best_error = _read_crops(
            processor, model, best_pool[:MAX_SEGMENTS]
        )
    else:
        lines, confidences = best_lines, best_confidences

    raw_text = "\n".join(lines)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    result = interpret_ocr_text(raw_text)
    if not looks_like_inventory_records([result]):
        logger.info("OCR TrOCR: se rechazó la imagen por no parecer del formato de inventario.")
        _reject_not_inventory()
    width, height = best_oriented.size
    logger.info(
        "OCR: %d segmento(s), %d línea(s), confianza media %.1f%%",
        len(best_pool),
        len(lines),
        confidence,
    )

    low_confidence = confidence < CONFIDENCE_THRESHOLD or not raw_text.strip()
    warnings = list(result["warnings"])
    if not raw_text.strip():
        warnings.insert(0, "No se reconoció texto (¿imagen borrosa o poco iluminada?).")
        if best_error:
            warnings.append(f"Detalle del lector: {best_error}")
    elif low_confidence:
        warnings.insert(0, "La lectura salió con poca confianza; revisá los campos antes de guardar.")

    return {
        "status": "low_confidence" if low_confidence else "ok",
        "engine": "trocr",
        "confidence": round(confidence, 1),
        "raw_text": raw_text,
        "width": width,
        "height": height,
        "records": [result],
        "warnings": warnings,
    }


def ocr_status() -> dict[str, object]:
    engine = selected_engine()
    if engine == "deepseek":
        available = bool(_deepseek_api_key())
        return {
            "available": available,
            "engine": "deepseek",
            "model": _deepseek_model(),
            "formats": SUPPORTED_FORMATS,
            "detail": None
            if available
            else "Falta DEEPSEEK_API_KEY. Configurá la clave en el archivo .env.",
        }

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
