"""Asistente del catálogo: responde preguntas sobre el inventario con DeepSeek.

El contexto se arma con los registros activos de la base (título, autor, pie de
imprenta, encuadernación y números de inventario). El modelo de
chat **más económico** de DeepSeek responde usando **solo** esos datos: si el
libro no está en el catálogo, o la pregunta no se entiende, lo dice en lugar de
inventar una respuesta.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path

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


# `deepseek-chat` es el modelo de chat más económico de la API (sin cadena de
# razonamiento). Se puede cambiar con CHATBOT_MODEL (o DEEPSEEK_CHAT_MODEL); con
# CHATBOT_API_KEY/CHATBOT_BASE_URL se puede apuntar a cualquier proveedor
# OpenAI-compatible (p. ej. un modelo gratuito).
DEFAULT_CHAT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_CONTEXT_CHARS = 48_000
MAX_MATCHES = 60
MAX_HISTORY = 8
MAX_HISTORY_CHARS = 2_000
CONTEXT_DIRNAME = "Entrevistas_Contexto"
MAX_CONTEXT_FILE_CHARS = 20_000
MAX_LIBRARY_CONTEXT_CHARS = 30_000
CONTEXT_SUFFIXES = (".md", ".markdown", ".txt")
_CONTEXT_CACHE: dict[str, object] = {"signature": None, "text": "", "count": 0}

_SYSTEM_PROMPT = (
    "Sos el asistente bibliotecario de la Biblioteca Provincial N° 349 (BP 349). "
    "Respondés en español, con claridad y brevedad, y usás ÚNICAMENTE estas dos "
    "fuentes, que se incluyen en el mensaje:\n"
    "1) CONOCIMIENTO INSTITUCIONAL: cómo funciona la biblioteca puertas adentro "
    "(préstamos, asociación, sanciones, horarios, computadoras, criterios internos).\n"
    "2) INVENTARIO: el catálogo en vivo de libros y ejemplares.\n"
    "Reglas obligatorias:\n"
    "- No inventes políticas, títulos, autores ni números de inventario.\n"
    "- Para saber si existe un libro o cuántos ejemplares hay, usá el INVENTARIO; si no "
    "aparece, respondé que no lo encontraste y sugerí revisar el título o el autor.\n"
    "- Para trámites y servicios (asociarse, préstamos, sanciones, horarios, netbooks, "
    "material especial) usá el CONOCIMIENTO INSTITUCIONAL.\n"
    "- Si la pregunta no está cubierta por ninguna de las dos fuentes, o es confusa, "
    "decí que no lo sabés y pedí que la reformulen; no adivines.\n"
    "- Si el conocimiento institucional y el inventario se contradicen sobre la "
    "disponibilidad de un libro, priorizá el inventario (es el estado actual).\n"
    "- Cada ficha del inventario tiene el formato: números | título | autor | pie de "
    "imprenta | encuadernación. Un rango como 62-71 son ejemplares del mismo registro; "
    "los números se muestran como INV-000062.\n"
    "- Del ejemplar solo se conoce la encuadernación u observaciones: no inventes estado "
    "físico, préstamos ni disponibilidad.\n"
    "- Si te piden totales, usá el resumen del inventario que se incluye.\n"
    "Formato de la respuesta:\n"
    "- Escribí en Markdown, con un tono cálido, claro y humano, como una bibliotecaria.\n"
    "- Empezá con una frase breve que responda directamente; después, si hace falta, ampliá.\n"
    "- Usá **negrita** para los datos clave (horarios, fechas, números de inventario, títulos).\n"
    "- Para listar varios libros, usá una tabla de Markdown con las columnas «Nº inv.» y "
    "«Título» (o «Autor» si viene al caso).\n"
    "- Para pasos o listas cortas, usá viñetas.\n"
    "- Sé concisa: no repitas la pregunta, no agregues secciones vacías ni relleno."
)

_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a",
    "que", "qué", "cual", "cuál", "cuales", "cuáles", "cuanto", "cuánto", "cuanta",
    "cuánta", "cuantos", "cuántos", "cuantas", "cuántas", "como", "cómo", "donde",
    "dónde", "hay", "tiene", "tienen", "tenes", "tenés", "esta", "está", "estan",
    "están", "es", "son", "por", "para", "con", "sin", "sobre", "me", "te", "se",
    "mi", "tu", "su", "y", "o", "u", "en", "algun", "algún", "alguna", "algunos",
    "algunas", "libro", "libros", "ejemplar", "ejemplares", "inventario", "numero",
    "número", "numeros", "números", "buscar", "buscame", "búscame", "quiero",
    "necesito", "podes", "podés", "podrias", "podrías", "decime", "decime", "sabes",
    "sabés", "favor", "gracias", "hola",
}


class ChatbotError(Exception):
    """La consulta no se pudo responder."""


class ChatbotUnavailable(ChatbotError):
    """Falta la API key o httpx no está disponible."""


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    without_accents = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def format_inventory_number(number: int) -> str:
    return f"INV-{int(number):06d}"


def compact_inventory_numbers(numbers) -> str:
    values = sorted({int(number) for number in numbers})
    if not values:
        return "sin número"
    parts: list[str] = []
    start = previous = values[0]
    for number in values[1:]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(_range_label(start, previous))
        start = previous = number
    parts.append(_range_label(start, previous))
    return ", ".join(parts)


def _range_label(start: int, end: int) -> str:
    if start == end:
        return format_inventory_number(start)
    return f"{format_inventory_number(start)}–{format_inventory_number(end)}"


def build_inventory_summary(records: list[dict[str, object]]) -> str:
    total_copies = sum(len(record.get("inventory_numbers") or []) for record in records)
    numbers = [
        number for record in records for number in (record.get("inventory_numbers") or [])
    ]
    lines = [
        f"Total: {len(records)} registros bibliográficos y {total_copies} ejemplares numerados.",
    ]
    if numbers:
        lines.append(
            "Rango de inventario: "
            f"{format_inventory_number(min(numbers))} a {format_inventory_number(max(numbers))}."
        )
    return "\n".join(lines)


def format_record(record: dict[str, object]) -> str:
    numbers = compact_inventory_numbers(record.get("inventory_numbers") or [])
    return " | ".join(
        (
            numbers,
            f"«{record.get('title') or '—'}»",
            str(record.get("author") or "—"),
            str(record.get("imprint") or "—"),
            str(record.get("binding") or "—"),
        )
    )


def _question_tokens(question: str) -> list[str]:
    tokens: list[str] = []
    for token in _normalize(question).split():
        if token in _STOPWORDS or len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _record_haystack(record: dict[str, object]) -> str:
    parts = [
        str(record.get("title") or ""),
        str(record.get("author") or ""),
        str(record.get("imprint") or ""),
        str(record.get("binding") or ""),
        str(record.get("inventory_source") or ""),
        " ".join(str(number) for number in (record.get("inventory_numbers") or [])),
        " ".join(
            format_inventory_number(number)
            for number in (record.get("inventory_numbers") or [])
        ),
    ]
    return _normalize(" ".join(parts))


def select_relevant_records(
    records: list[dict[str, object]], question: str, limit: int = MAX_MATCHES
) -> list[dict[str, object]]:
    tokens = _question_tokens(question)
    if not tokens:
        return list(records[:limit])

    scored: list[tuple[int, dict[str, object]]] = []
    for record in records:
        haystack = _record_haystack(record)
        score = sum(2 if len(token) > 3 else 1 for token in tokens if token in haystack)
        if score:
            scored.append((score, record))
    if not scored:
        return list(records[:limit])
    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in scored[:limit]]


def build_catalog_context(
    records: list[dict[str, object]], question: str
) -> tuple[str, str, int, bool]:
    """Devuelve (resumen, fichas, cantidad incluida, si se recortó)."""

    summary = build_inventory_summary(records)
    if not records:
        return summary, "El inventario está vacío: no hay registros cargados.", 0, False

    selected = list(records)
    truncated = False
    lines = [format_record(record) for record in selected]
    if len("\n".join(lines)) > MAX_CONTEXT_CHARS:
        selected = select_relevant_records(records, question)
        truncated = len(selected) < len(records)
        lines = [format_record(record) for record in selected]
        while len(lines) > 1 and len("\n".join(lines)) > MAX_CONTEXT_CHARS:
            lines.pop()
    return summary, "\n".join(lines), len(selected), truncated


def _sanitize_history(history) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history or []:
        if isinstance(item, dict):
            role = item.get("role")
            content = item.get("content")
        else:
            role = getattr(item, "role", None)
            content = getattr(item, "content", None)
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": str(content).strip()[:MAX_HISTORY_CHARS]})
    return messages[-MAX_HISTORY:]


def _api_key() -> str:
    key = _setting("CHATBOT_API_KEY")
    if key:
        return key
    # Si el chat apunta a otro proveedor, no reusamos la clave de DeepSeek.
    if _setting("CHATBOT_BASE_URL"):
        return ""
    return _setting("DEEPSEEK_API_KEY")


def _base_url() -> str:
    base = _setting("CHATBOT_BASE_URL") or _setting("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    return base.rstrip("/")


def _chat_model() -> str:
    return _setting("CHATBOT_MODEL") or _setting("DEEPSEEK_CHAT_MODEL", DEFAULT_CHAT_MODEL)


def context_directory() -> Path:
    """Carpeta con la base de conocimiento institucional (entrevistas, normas)."""

    configured = _setting("LIBRARY_CONTEXT_DIR")
    if configured:
        return Path(configured).expanduser()
    return _PROJECT_ROOT / CONTEXT_DIRNAME


def load_library_context() -> tuple[str, int]:
    """Lee los .md/.txt de la carpeta de contexto y los une en un bloque.

    El resultado se cachea y solo se vuelve a leer si cambia algún archivo, así
    el texto institucional se envía como prefijo estable (mejor caché de prompt).
    """

    directory = context_directory()
    try:
        files = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in CONTEXT_SUFFIXES
        )
    except OSError:
        return "", 0
    try:
        signature = tuple((str(path), path.stat().st_mtime_ns) for path in files)
    except OSError:
        return "", 0

    if _CONTEXT_CACHE["signature"] == signature:
        return str(_CONTEXT_CACHE["text"]), int(_CONTEXT_CACHE["count"])

    parts: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if content:
            parts.append(f"### {path.name}\n{content[:MAX_CONTEXT_FILE_CHARS]}")
    text = "\n\n".join(parts)
    if len(text) > MAX_LIBRARY_CONTEXT_CHARS:
        text = text[:MAX_LIBRARY_CONTEXT_CHARS]
    _CONTEXT_CACHE.update(signature=signature, text=text, count=len(parts))
    return text, len(parts)


def chatbot_status() -> dict[str, object]:
    available = bool(_api_key())
    _, context_files = load_library_context()
    return {
        "available": available,
        "model": _chat_model(),
        "context_files": context_files,
        "detail": None
        if available
        else "Falta CHATBOT_API_KEY (o DEEPSEEK_API_KEY). Configurá la clave en .env.",
    }


def answer_question(
    records: list[dict[str, object]],
    message: str,
    history=None,
) -> dict[str, object]:
    """Responde una pregunta sobre el inventario usando el modelo económico."""

    message = (message or "").strip()
    if not message:
        raise ChatbotError("Escribí una pregunta sobre el inventario.")

    api_key = _api_key()
    if not api_key:
        raise ChatbotUnavailable(
            "Falta la clave del asistente. Configurá CHATBOT_API_KEY "
            "(o DEEPSEEK_API_KEY) en el archivo .env."
        )

    summary, catalog, included, truncated = build_catalog_context(records, message)
    note = (
        f"\n(Se muestran {included} de {len(records)} fichas por relevancia; "
        "si la respuesta no está, decí que no la encontraste.)"
        if truncated
        else ""
    )
    user_content = (
        f"Resumen del inventario:\n{summary}\n\n"
        f"Fichas del catálogo (números | título | autor | pie de imprenta | encuadernación):\n"
        f"{catalog}{note}\n\n"
        f"Pregunta: {message}"
    )

    library_context, context_files = load_library_context()
    if library_context:
        system_content = (
            f"{_SYSTEM_PROMPT}\n\n"
            "# CONOCIMIENTO INSTITUCIONAL DE LA BIBLIOTECA\n"
            "(Cómo funciona la biblioteca puertas adentro. Usalo para responder "
            "sobre trámites, servicios y normas internas.)\n"
            f"{library_context}"
        )
    else:
        system_content = _SYSTEM_PROMPT

    messages = [{"role": "system", "content": system_content}]
    messages.extend(_sanitize_history(history))
    messages.append({"role": "user", "content": user_content})

    model = _chat_model()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "stream": False,
    }

    try:
        import httpx
    except ImportError as error:
        raise ChatbotUnavailable("Falta la librería httpx en el servidor.") from error

    timeout = float(_setting("DEEPSEEK_TIMEOUT", "60"))
    try:
        response = httpx.post(
            f"{_base_url()}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except Exception as error:  # noqa: BLE001 - red/DNS/timeout
        raise ChatbotError(f"No pude contactar la API del asistente: {error}") from error

    if response.status_code >= 400:
        detail = response.text.strip()[:500]
        if response.status_code in (401, 402, 403):
            raise ChatbotUnavailable(
                f"El proveedor del asistente rechazó la clave o el saldo: {detail}"
            )
        if response.status_code == 429:
            raise ChatbotError(
                "El asistente está recibiendo muchas consultas en este momento. "
                "Esperá unos segundos y volvé a intentar."
            )
        raise ChatbotError(
            f"La API del asistente respondió con el estado {response.status_code}: {detail}"
        )

    try:
        body = response.json()
        reply = str(body["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ChatbotError("La respuesta del asistente no tuvo el formato esperado.") from error

    if not reply:
        raise ChatbotError("El modelo no devolvió una respuesta.")

    logger.info(
        "ChatBot (%s): %d fichas y %d archivo(s) de contexto institucional.",
        model,
        included,
        context_files,
    )
    return {"reply": reply, "model": model, "context_files": context_files}
