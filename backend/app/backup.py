"""Respaldos portables del inventario en formato JSON.

El respaldo guarda el catálogo completo (registros activos y los que están en la
papelera) con sus números de inventario. No depende del motor de base de datos,
así que sirve para mover los datos entre MySQL y SQLite o para conservar una
copia antes de una importación masiva.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.inventory import record_to_dict

BACKUP_FORMAT = "biblioteca-inventario"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 20_000_000
MAX_BACKUP_RECORDS = 100_000

TEXT_FIELDS = {
    "title": 500,
    "author": 500,
    "imprint": 10_000,
    "binding": 300,
    "inventory_source": 120,
}


class BackupError(ValueError):
    """El archivo recibido no es un respaldo válido de este sistema."""


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat()


def _parse_datetime(value: Any, position: int) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise BackupError(
            f"El registro {position} tiene una fecha de eliminación inválida."
        )
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise BackupError(
            f"El registro {position} tiene una fecha de eliminación inválida."
        ) from error
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def build_backup(records, *, generated_at: datetime | None = None) -> dict[str, Any]:
    """Arma el documento JSON de respaldo a partir de los registros del catálogo."""

    items: list[dict[str, Any]] = []
    for record in records:
        data = record_to_dict(record)
        data["deleted_at"] = _format_datetime(data.get("deleted_at"))
        items.append(data)

    active = [item for item in items if item["deleted_at"] is None]
    trashed = [item for item in items if item["deleted_at"] is not None]
    created_at = generated_at or datetime.now(timezone.utc)
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": created_at.isoformat(timespec="seconds"),
        "summary": {
            "records": len(active),
            "copies": sum(len(item["inventory_numbers"]) for item in active),
            "trashed_records": len(trashed),
            "trashed_copies": sum(
                len(item["inventory_numbers"]) for item in trashed
            ),
        },
        "records": items,
    }


def parse_backup(text: str) -> list[dict[str, Any]]:
    """Valida un respaldo JSON y devuelve los registros listos para guardar.

    Los números de inventario deben ser únicos entre los registros activos; los
    registros en la papelera pueden repetir números, igual que en el catálogo.
    """

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise BackupError("El archivo no es un respaldo JSON válido.") from error

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if payload.get("format") != BACKUP_FORMAT:
            raise BackupError(
                "El archivo no es un respaldo de este sistema. "
                "Descargalo con el botón «Respaldar»."
            )
        version = payload.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise BackupError("El respaldo no indica una versión válida.")
        if version > BACKUP_VERSION:
            raise BackupError(
                "El respaldo fue creado con una versión más nueva del sistema. "
                "Actualizá la aplicación para restaurarlo."
            )
        items = payload.get("records")
        if not isinstance(items, list):
            raise BackupError("El respaldo no contiene una lista de registros.")
    else:
        raise BackupError("El archivo no tiene el formato de un respaldo válido.")

    if not items:
        raise BackupError("El respaldo no contiene registros para restaurar.")
    if len(items) > MAX_BACKUP_RECORDS:
        maximum = f"{MAX_BACKUP_RECORDS:,}".replace(",", ".")
        raise BackupError(
            f"El respaldo supera el máximo de {maximum} registros."
        )

    parsed = [
        _parse_record(item, position)
        for position, item in enumerate(items, start=1)
    ]

    active_numbers: dict[int, int] = {}
    for position, item in enumerate(parsed, start=1):
        if item["deleted_at"] is not None:
            continue
        for number in item["inventory_numbers"]:
            if number in active_numbers:
                raise BackupError(
                    f"INV-{number:06d} aparece en dos registros activos "
                    f"(filas {active_numbers[number]} y {position})."
                )
            active_numbers[number] = position

    return parsed


def _parse_record(item: Any, position: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise BackupError(f"El registro {position} no tiene el formato esperado.")

    record: dict[str, Any] = {}
    for field, limit in TEXT_FIELDS.items():
        value = item.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise BackupError(
                f"El registro {position} tiene un valor inválido en «{field}»."
            )
        if len(value) > limit:
            raise BackupError(
                f"El registro {position} supera el máximo de {limit} caracteres "
                f"en «{field}»."
            )
        record[field] = value

    numbers = item.get("inventory_numbers")
    if numbers is None:
        numbers = []
    if not isinstance(numbers, list):
        raise BackupError(
            f"El registro {position} no tiene una lista de números de inventario."
        )
    clean_numbers: list[int] = []
    for number in numbers:
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise BackupError(
                f"El registro {position} tiene un número de inventario inválido."
            )
        clean_numbers.append(number)
    if len(clean_numbers) != len(set(clean_numbers)):
        raise BackupError(
            f"El registro {position} contiene números de inventario repetidos."
        )
    record["inventory_numbers"] = sorted(clean_numbers)
    record["deleted_at"] = _parse_datetime(item.get("deleted_at"), position)
    return record
