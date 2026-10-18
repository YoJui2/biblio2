"""Asistente del catálogo: responde preguntas sobre el inventario con DeepSeek.

El contexto se arma con los registros activos de la base (sección, título,
autor, pie de imprenta, encuadernación y números de inventario). El modelo de
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
# razonamiento). Se puede cambiar con DEEPSEEK_CHAT_MODEL si hace falta.
DEFAULT_CHAT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_CONTEXT_CHARS = 48_000
MAX_MATCHES = 60
MAX_HISTORY = 8
MAX_HISTORY_CHARS = 2_000

_SYSTEM_PROMPT = (
    "Sos el asistente bibliotecario del inventario de una biblioteca. "
    "Respondés en español, con claridad y brevedad, y usás ÚNICAMENTE los datos "
    "del inventario que se incluyen en el mensaje.\n"
    "Reglas obligatorias:\n"
    "- No inventes títulos, autores, secciones ni números de inventario que no figuren "
    "en el contexto.\n"
    "- Si te preguntan por un libro que no aparece en las fichas, respondé que no lo "
    "encontraste en el inventario y, si podés, sugerí revisar el título, el autor o la "
    "sección.\n"
    "- Si la pregunta es confusa o no se entiende, pedí que la reformulen de forma "
    "concreta en lugar de adivinar.\n"
    "- Cada ficha tiene el formato: números de inventario | sección | título | autor | "
    "pie de imprenta | encuadernación. Un rango como 62-71 son ejemplares del mismo "
    "registro; los números se muestran como INV-000062.\n"
    "- Lo único que se sabe de cada ejemplar es la encuadernación u observaciones: no "
    "inventes disponibilidad, préstamos ni estado físico.\n"
    "- Si te piden totales, usá el resumen del inventario que se incluye."
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
    sections: dict[str, dict[str, int]] = {}
    for record in records:
        section = str(record.get("section") or "Sin sección").strip() or "Sin sección"
        stats = sections.setdefault(section, {"records": 0, "copies": 0})
        stats["records"] += 1
        stats["copies"] += len(record.get("inventory_numbers") or [])

    lines = [
        f"Total: {len(records)} registros bibliográficos y {total_copies} ejemplares numerados.",
    ]
    if sections:
        lines.append("Secciones:")
        for section, stats in sorted(sections.items(), key=lambda item: item[0].lower()):
            lines.append(
                f"- {section}: {stats['records']} registros, {stats['copies']} ejemplares."
            )
    return "\n".join(lines)


def format_record(record: dict[str, object]) -> str:
    numbers = compact_inventory_numbers(record.get("inventory_numbers") or [])
    return " | ".join(
        (
            numbers,
            str(record.get("section") or "—"),
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
        str(record.get("section") or ""),
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
    return _setting("DEEPSEEK_API_KEY")


def _base_url() -> str:
    return _setting("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _chat_model() -> str:
    return _setting("DEEPSEEK_CHAT_MODEL", DEFAULT_CHAT_MODEL)


def chatbot_status() -> dict[str, object]:
    available = bool(_api_key())
    return {
        "available": available,
        "model": _chat_model(),
        "detail": None
        if available
        else "Falta DEEPSEEK_API_KEY. Configurá la clave en el archivo .env.",
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
            "Falta la clave de DeepSeek. Configurá DEEPSEEK_API_KEY en el archivo .env."
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
        f"Fichas del catálogo (números | sección | título | autor | pie de imprenta | encuadernación):\n"
        f"{catalog}{note}\n\n"
        f"Pregunta: {message}"
    )
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
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
        raise ChatbotError(f"No pude contactar la API de DeepSeek: {error}") from error

    if response.status_code >= 400:
        detail = response.text.strip()[:500]
        if response.status_code in (401, 402, 403):
            raise ChatbotUnavailable(
                f"La API de DeepSeek rechazó la clave o el saldo: {detail}"
            )
        raise ChatbotError(
            f"La API de DeepSeek respondió con el estado {response.status_code}: {detail}"
        )

    try:
        body = response.json()
        reply = str(body["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ChatbotError("La respuesta de DeepSeek no tuvo el formato esperado.") from error

    if not reply:
        raise ChatbotError("El modelo no devolvió una respuesta.")

    logger.info("ChatBot DeepSeek (%s): %d fichas en contexto.", model, included)
    return {"reply": reply, "model": model}
